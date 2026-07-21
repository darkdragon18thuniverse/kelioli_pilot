Here is the complete, updated markdown document for your curltester.md file.
It has been expanded to test the full end-to-end operational flow with the correct database schema validation patterns, specific role provisionings, and the newly added metrics/listing endpoints.
Kelioli API - End-to-End Core Verification Suite
This suite provides simple, structured cURL commands to validate the multi-tenant application pipeline. Run these sequentially from the project root directory.
Phase 1: Authentication & Token Retrieval
1. Exchange Superadmin Credentials for Access Session Token
Log in using the default Superadmin setup details.
curl -i -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=vinamra@curigonglobal.com" \
  -d "password=YourSecurePasswordHere"

Action Item: Copy the access_token string value from the JSON response. Replace SUPERADMIN_JWT_TOKEN in the commands below with that string.
2. Verify Superadmin Session Context
curl -i -X GET "http://127.0.0.1:8000/api/v1/auth/me" \
  -H "Authorization: Bearer SUPERADMIN_JWT_TOKEN"

Phase 2: System Tenant & Admin Provisioning (Superadmin Only)
3. Initialize Parent Organization (Tenant Group)
curl -i -X POST "http://127.0.0.1:8000/api/v1/admin/organizations" \
  -H "Authorization: Bearer SUPERADMIN_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Curigon Medical Inc.",
    "slug": "curigon",
    "billing_email": "billing@curigon.com",
    "tier": "enterprise",
    "per_minute_cost": 0.15,
    "infra_fixed_cost": 49.00
  }'

Action Item: Take note of the returned organization "id" (e.g., 1). Replace TARGET_ORG_ID below with this integer.
4. Provision a Tenant Admin Account (Role 2)
Create an admin bound entirely within the target organization boundary.
curl -i -X POST "http://127.0.0.1:8000/api/v1/admin/users" \
  -H "Authorization: Bearer SUPERADMIN_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role_id": 2,
    "organization_id": TARGET_ORG_ID,
    "department_id": null,
    "name": "Curigon Admin User",
    "email": "admin@curigon.com",
    "password": "SecurePassword2026!"
  }'

5. Fetch Global Admin Infrastructure Summary
curl -i -X GET "http://127.0.0.1:8000/api/v1/admin/summary" \
  -H "Authorization: Bearer SUPERADMIN_JWT_TOKEN"

6. Fetch Active Organizations List
curl -i -X GET "http://127.0.0.1:8000/api/v1/admin/organizations" \
  -H "Authorization: Bearer SUPERADMIN_JWT_TOKEN"

Phase 3: Tenant Operations (Tenant Admin Login)
7. Login as the Tenant Admin
curl -i -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@curigon.com" \
  -d "password=SecurePassword2026!"

Action Item: Copy this token value. Replace ADMIN_JWT_TOKEN in the following section with this credential string.
8. Create Sandbox Department (Tenant Unit)
curl -i -X POST "http://127.0.0.1:8000/api/v1/admin/departments" \
  -H "Authorization: Bearer ADMIN_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_id": TARGET_ORG_ID,
    "name": "Radiology Division",
    "slug": "radiology"
  }'

Action Item: Take note of the returned department "id" (e.g., 1). Replace TARGET_DEPT_ID in subsequent steps.
9. Provision a Tenant Manager (Role 3)
curl -i -X POST "http://127.0.0.1:8000/api/v1/admin/users" \
  -H "Authorization: Bearer ADMIN_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role_id": 3,
    "organization_id": TARGET_ORG_ID,
    "department_id": TARGET_DEPT_ID,
    "name": "Curigon Manager",
    "email": "manager@curigon.com",
    "password": "SecurePassword2026!"
  }'

10. Provision a Tenant Agent (Role 4)
curl -i -X POST "http://127.0.0.1:8000/api/v1/admin/users" \
  -H "Authorization: Bearer ADMIN_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role_id": 4,
    "organization_id": TARGET_ORG_ID,
    "department_id": TARGET_DEPT_ID,
    "name": "Vinamra Mattoo",
    "email": "vinamra@curigon.com",
    "password": "SecurePassword2026!"
  }'

Phase 4: Ingestion Pipeline Processing
11. Process Audio Batches via CSV Ingestion
Submits a data batch for full transcription and evaluation parsing using the Agent's credentials.
curl -i -X POST "http://127.0.0.1:8000/api/v1/calls/process-csv" \
  -H "Authorization: Bearer ADMIN_JWT_TOKEN" \
  -F "file=@test.csv"

Appendix: Automated Local Test CSV Generation
To quickly create a valid structural artifact matching your pipeline requirements, execute this single-line command in your terminal to generate a mock test.csv file inside your project root:
printf "audio_url\nhttps://github.com/rafaelreis-7/datasets/raw/main/audio_samples/sample1.mp3\n" > test.csv