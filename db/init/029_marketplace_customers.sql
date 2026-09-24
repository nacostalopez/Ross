-- Mercado Libre masks buyer email/phone, so its connector identifies a
-- customer by a namespaced buyer id ("ml:123") instead — see
-- app/services/customers.py::resolve_customer_id. That lookup runs once
-- per ingested order, so it gets an index like email_hash's.
CREATE INDEX IF NOT EXISTS idx_customers_store_external
    ON customers(store_id, external_customer_id)
    WHERE external_customer_id IS NOT NULL;
