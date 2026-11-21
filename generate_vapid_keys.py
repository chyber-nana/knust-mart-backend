"""Prints a VAPID key pair for phone push notifications. Run once, put the values in .env / Render.
Usage: python generate_vapid_keys.py"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
key = ec.generate_private_key(ec.SECP256R1())
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
private = key.private_numbers().private_value.to_bytes(32, "big")
print("VAPID_PUBLIC_KEY=" + b64(public))
print("VAPID_PRIVATE_KEY=" + b64(private))
print("VAPID_SUBJECT=mailto:you@example.com   # change to your real contact email")
