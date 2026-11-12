-- Canonical schema reference for the current marketplace.
-- Do not apply this file to a database. Use `alembic upgrade head`.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  user_type TEXT NOT NULL CHECK (user_type IN ('student', 'external')),
  is_verified_student BOOLEAN NOT NULL DEFAULT FALSE,
  is_email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  is_verified BOOLEAN NOT NULL DEFAULT FALSE,
  otp_code VARCHAR(6),
  otp_expires_at TIMESTAMPTZ,
  is_courier BOOLEAN NOT NULL DEFAULT FALSE,
  is_banned BOOLEAN NOT NULL DEFAULT FALSE,
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  hostel_or_business_address TEXT,
  store_name TEXT,
  store_bio TEXT,
  logo_url TEXT,
  logo_position_x INTEGER NOT NULL DEFAULT 50 CHECK (logo_position_x BETWEEN 0 AND 100),
  logo_position_y INTEGER NOT NULL DEFAULT 50 CHECK (logo_position_y BETWEEN 0 AND 100),
  banner_url TEXT,
  banner_position_x INTEGER NOT NULL DEFAULT 50 CHECK (banner_position_x BETWEEN 0 AND 100),
  banner_position_y INTEGER NOT NULL DEFAULT 50 CHECK (banner_position_y BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE categories (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE
);

CREATE TABLE products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_id UUID NOT NULL REFERENCES users(id),
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  price NUMERIC(10,2) NOT NULL,
  condition TEXT,
  hall_location TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'sold', 'reserved')),
  is_promoted BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE product_images (
  id SERIAL PRIMARY KEY,
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  image_url TEXT NOT NULL
);

CREATE TABLE campus_ads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  image_url TEXT NOT NULL,
  alt_text VARCHAR(160) NOT NULL,
  link_url TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE email_verifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) NOT NULL,
  otp_code VARCHAR(10),
  verification_token VARCHAR(128) UNIQUE,
  expires_at TIMESTAMPTZ NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE chat_messages (
  id BIGSERIAL PRIMARY KEY,
  product_id UUID NOT NULL REFERENCES products(id),
  sender_id UUID NOT NULL REFERENCES users(id),
  receiver_id UUID NOT NULL REFERENCES users(id),
  message TEXT NOT NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at TIMESTAMPTZ
);

CREATE TABLE notifications (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  notification_type VARCHAR(32) NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  link TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at TIMESTAMPTZ
);
CREATE INDEX ix_notifications_user_unread_created
  ON notifications(user_id, read_at, created_at);

CREATE TABLE deliveries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id UUID NOT NULL REFERENCES products(id),
  buyer_id UUID NOT NULL REFERENCES users(id),
  courier_id UUID REFERENCES users(id),
  fulfillment_type TEXT NOT NULL DEFAULT 'courier' CHECK (fulfillment_type IN ('courier', 'pickup')),
  pickup_location TEXT NOT NULL,
  dropoff_location TEXT NOT NULL,
  delivery_fee NUMERIC(8,2) NOT NULL,
  courier_available_at TIMESTAMPTZ,
  delivery_qr_token TEXT NOT NULL UNIQUE,
  pin_code CHAR(6) NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('awaiting_seller', 'pending', 'ready_for_pickup', 'accepted', 'in_transit', 'delivered', 'failed', 'disputed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE reviews (
  id SERIAL PRIMARY KEY,
  delivery_id UUID NOT NULL UNIQUE REFERENCES deliveries(id),
  reviewer_id UUID NOT NULL REFERENCES users(id),
  vendor_id UUID NOT NULL REFERENCES users(id),
  rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment TEXT
);

CREATE TABLE appeals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  reviewed_by UUID REFERENCES users(id),
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  rejection_reason TEXT
);
CREATE UNIQUE INDEX appeals_one_pending_per_user_idx ON appeals (user_id) WHERE status = 'pending';

CREATE TABLE auth_rate_limits (
  id SERIAL PRIMARY KEY,
  action VARCHAR(32) NOT NULL,
  subject VARCHAR(320) NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  attempts INTEGER NOT NULL,
  CONSTRAINT auth_rate_limits_bucket_key UNIQUE (action, subject, window_start)
);

CREATE TABLE courier_payouts (
  id SERIAL PRIMARY KEY,
  courier_id UUID NOT NULL REFERENCES users(id),
  delivery_id UUID NOT NULL UNIQUE REFERENCES deliveries(id),
  amount NUMERIC(8,2) NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
