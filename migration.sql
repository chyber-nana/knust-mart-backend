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
