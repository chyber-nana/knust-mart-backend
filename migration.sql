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
