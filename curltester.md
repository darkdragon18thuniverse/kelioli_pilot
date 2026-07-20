# Kelioli API - cURL Tester

Simple cURL commands to test the Kelioli API endpoints from the project root directory.

---

## 1. Health Check
Verifies that the application is running and responsive.

```bash
curl -X GET "http://127.0.0.1:8000/health"

curl -X POST "http://127.0.0.1:8000/process-csv" -F "file=@test.csv"