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
