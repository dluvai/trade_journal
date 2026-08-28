"""Stripe integration: customers, Checkout Sessions, webhooks, and admin actions.

Uses Stripe-hosted Checkout (redirect), never Stripe Elements -- this app
must never see or store a raw card number. Every persistent reference
(customer id, subscription id, payment method id) is an opaque Stripe id
stored in Turso via db.py. This is the only module that imports `stripe`;
server.py routes call into these functions instead of the SDK directly.
"""
import os
from datetime import datetime, timezone

import stripe

import db

def _client():
    # Read lazily, not as a module constant, since server.py imports this module before loading its .env.
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    return stripe


def create_stripe_plan(label, description, unit_amount_cents):
    client = _client()
    product = client.Product.create(name=label, description=description or None)
    price = client.Price.create(
        product=product.id, unit_amount=unit_amount_cents, currency="usd",
        recurring={"interval": "month"},
    )
    return product.id, price.id


def archive_stripe_plan(stripe_price_id, stripe_product_id):
    client = _client()
    # Price must be deactivated before the Product -- Stripe rejects deactivating a Product while
    # it still has an active Price. Existing subscriptions already on this Price are unaffected by
    # either call; Stripe only enforces `active` when creating *new* Prices/Subscriptions.
    client.Price.modify(stripe_price_id, active=False)
    client.Product.modify(stripe_product_id, active=False)


def ensure_stripe_customer(user):
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]
    client = _client()
    customer = client.Customer.create(
        email=user.get("email") or None,
        name=" ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or user["username"],
        metadata={"app_user_id": str(user["id"])},
    )
    db.set_stripe_customer_id(user["id"], customer.id)
    return customer.id


def create_checkout_session(user, plan, success_url, cancel_url):
    client = _client()
    customer_id = ensure_stripe_customer(user)
    return client.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": plan["stripe_price_id"], "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"app_user_id": str(user["id"]), "tier_key": plan["key"]},
    )


def construct_webhook_event(payload, sig_header):
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)


def _period_end_iso(subscription_obj):
    period_end = subscription_obj.get("current_period_end")
    if not period_end:
        return None
    return datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()


def _upsert_from_subscription_object(sub):
    user = db.get_user_by_stripe_customer_id(sub["customer"])
    if not user:
        return
    tier_key = (sub.get("metadata") or {}).get("tier_key")
    price_id = sub["items"]["data"][0]["price"]["id"] if sub.get("items", {}).get("data") else None
    db.upsert_subscription(
        user_id=user["id"],
        stripe_subscription_id=sub["id"],
        stripe_price_id=price_id,
        tier_key=tier_key,
        status=sub["status"],
        current_period_end=_period_end_iso(sub),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
    )


def handle_webhook_event(event):
    # Idempotent -- safe to call twice for the same event, since Stripe retries on any non-2xx response.
    event_type = event["type"]
    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        subscription_id = session_obj.get("subscription")
        if subscription_id:
            client = _client()
            sub = client.Subscription.retrieve(subscription_id)
            _upsert_from_subscription_object(sub)
    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        _upsert_from_subscription_object(event["data"]["object"])
    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        user = db.get_user_by_stripe_customer_id(sub["customer"])
        if user:
            db.upsert_subscription(
                user_id=user["id"], stripe_subscription_id=sub["id"],
                stripe_price_id=None, tier_key=(sub.get("metadata") or {}).get("tier_key"),
                status="canceled", current_period_end=_period_end_iso(sub), cancel_at_period_end=True,
            )


def list_payment_methods(stripe_customer_id):
    if not stripe_customer_id:
        return []
    client = _client()
    methods = client.PaymentMethod.list(customer=stripe_customer_id, type="card")
    return [
        {"id": m.id, "brand": m.card.brand, "last4": m.card.last4, "exp_month": m.card.exp_month, "exp_year": m.card.exp_year}
        for m in methods.data
    ]


def detach_payment_method(payment_method_id):
    client = _client()
    client.PaymentMethod.detach(payment_method_id)


def cancel_subscription(stripe_subscription_id, at_period_end=True):
    client = _client()
    if at_period_end:
        client.Subscription.modify(stripe_subscription_id, cancel_at_period_end=True)
    else:
        client.Subscription.delete(stripe_subscription_id)
