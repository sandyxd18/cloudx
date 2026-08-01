---
name: API Endpoint Standard
description: Guidelines and patterns for implementing new API endpoints in CloudX services (Keystone, Nova, Neutron, Glance, Cinder, Placement).
---

# API Endpoint Standard

When implementing new API endpoints for CloudX services based on the OpenStack parity roadmap, follow these guidelines to ensure consistency and maintainability:

## 1. RESTful Principles
- Use standard HTTP methods: `GET` (read), `POST` (create), `PUT`/`PATCH` (update), `DELETE` (remove).
- Use plural nouns for resources (e.g., `/servers`, `/volumes`, `/networks`).
- Nest sub-resources logically (e.g., `/servers/{id}/tags`, `/images/{id}/members`).

## 2. Authentication and Authorization
- All protected endpoints must validate the JWT token. Check if the project uses a standard `auth_middleware.py`.
- Enforce Role-Based Access Control (RBAC). Until `oslo.policy` is implemented, check roles manually (e.g., `if token.role != "admin": raise HTTPException(...)`).
- Support project scoping (and future domain/system scoping). Filter resources so users only see data belonging to their project unless they have an admin role or the resource is shared.

## 3. Database Interactions
- Use SQLAlchemy sessions properly. Ensure database operations are wrapped in transactions.
- Never let workers directly access the database if a conductor pattern is intended (e.g., Nova compute-worker).

## 4. Cross-Cutting Concerns
- **Pagination**: Implement cursor-based or offset-based pagination using standard query parameters: `limit`, `marker`, `sort`.
- **Request ID**: Ensure the `X-OpenStack-Request-Id` header is returned in every response to aid tracing.
- **Error Handling**: Return standard OpenStack-like JSON error responses with the correct HTTP status codes (400, 401, 403, 404, 409).

## 5. Consistency
- Before adding an endpoint, review similar endpoints in the current service's `main.py` file and mimic their style, naming conventions, and validation patterns.
- Follow FastAPI standards (Pydantic models for request/response validation).
