---
name: Database Migration Standard
description: Guidelines for adding new tables, columns, or modifying the database schema in CloudX.
---

# Database Migration Standard

The CloudX project uses a single shared PostgreSQL database (`mini_openstack`) for all services. When implementing the roadmap features (e.g., Service Catalog, Quotas, Subnet Pools), database schema changes are required.

## 1. Defining Models
- Define new SQLAlchemy models in the relevant service's codebase (typically in `main.py` or a dedicated models module).
- Use clear, descriptive, plural table names (e.g., `__tablename__ = "services"`).
- Use standard `snake_case` for column names.
- Define proper primary keys, usually `String` UUIDs or auto-incrementing integers.
- Set up explicit Foreign Key relationships and indices where needed for performance.

## 2. Applying Schema Changes
- Verify how the project handles schema creation. Typically, it uses `Base.metadata.create_all(bind=engine)` on application startup.
- If `create_all` is used, simply defining the model and ensuring it is imported during startup will create new tables.
- **Caution for Column Additions/Modifications**: `create_all` does not alter existing tables. If you need to add columns to an existing table (e.g., adding `multiattach` to `volumes`), you may need to write a manual SQL `ALTER TABLE` script or integrate a migration tool like Alembic if it is adopted in the future.
- Always prefer additive schema changes over destructive ones to prevent breaking other services sharing the database.

## 3. Quota and Constraints
- When implementing quotas or constraints, try to handle race conditions gracefully. For instance, updating quota usages should use atomic database operations or row-level locking (`with_for_update()`) if supported.
