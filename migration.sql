-- Run once in the Neon SQL editor.
begin;
alter table products add column if not exists stock integer;

update products p set stock = case
  when p.category = 'Services' then null
  when p.status = 'active' then 1
  when p.status = 'reserved' and not exists (
       select 1 from deliveries d where d.product_id = p.id
       and d.status in ('awaiting_seller','pending','ready_for_pickup','accepted','in_transit')) then 1
  else 0 end
where p.stock is null;

alter table products add constraint products_stock_check check (stock is null or stock >= 0);
commit;

-- Courier registration (GHS 10, free for the first 5). Run once.
begin;
alter table users add column if not exists courier_activated_at timestamptz;
alter table users add column if not exists courier_activation_type text;
create index if not exists ix_users_courier_activation_type on users (courier_activation_type);
alter table users add constraint users_courier_activation_type_check
  check (courier_activation_type in ('free_launch', 'paid', 'legacy'));
-- Existing couriers are grandfathered in so they aren't charged retroactively.
update users set courier_activated_at = now(), courier_activation_type = 'legacy'
 where is_courier and courier_activated_at is null;
commit;

-- Courier offers (courier sets price, buyer chooses). Run once.
begin;
create table if not exists delivery_offers (
  id uuid primary key,
  delivery_id uuid not null references deliveries(id) on delete cascade,
  courier_id uuid not null references users(id),
  price numeric(8,2) not null check (price > 0),
  status text not null default 'open' check (status in ('open', 'chosen', 'rejected', 'withdrawn')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint delivery_offers_delivery_courier_key unique (delivery_id, courier_id)
);
create index if not exists ix_delivery_offers_delivery_id on delivery_offers (delivery_id);
create index if not exists ix_delivery_offers_courier_id on delivery_offers (courier_id);
commit;

-- Phone push subscriptions + settle existing courier payouts. Run once.
begin;
create table if not exists push_subscriptions (
  id serial primary key,
  user_id uuid not null references users(id) on delete cascade,
  endpoint text not null,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now(),
  constraint push_subscriptions_endpoint_key unique (endpoint)
);
create index if not exists ix_push_subscriptions_user_id on push_subscriptions (user_id);
-- A completed handover means the courier was paid in person.
update courier_payouts set status = 'paid' where status = 'pending';
commit;

-- Optional colours/sizes on listings
alter table products add column if not exists colors jsonb not null default '[]'::jsonb;
alter table products add column if not exists sizes jsonb not null default '[]'::jsonb;

-- Default courier flow: standard price, first courier accepts. Buyer can opt in to offers.
alter table deliveries add column if not exists choose_courier boolean not null default false;

-- Buyer's chosen colour/size on an order
alter table deliveries add column if not exists chosen_color text;
alter table deliveries add column if not exists chosen_size text;

-- Seller handover confirmation + buyer's directions note
alter table deliveries add column if not exists seller_released_at timestamptz;
alter table deliveries add column if not exists dropoff_note text;
