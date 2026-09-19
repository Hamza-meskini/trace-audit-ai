import httpx

def test_api():
    base = "http://localhost:8000"
    
    # 1. Projects
    r = httpx.get(f"{base}/api/projects", timeout=5.0)
    print(f"GET /api/projects: status={r.status_code}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    projects = r.json()
    print(f"  Count: {len(projects)}")
    for p in projects:
        print(f"  - {p['audit_id']}: {p['name']}")

    # 2. AI Settings
    r = httpx.get(f"{base}/api/settings/ai", timeout=5.0)
    print(f"GET /api/settings/ai: status={r.status_code}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    settings_data = r.json()
    print(f"  Active model: {settings_data.get('active_model_id')}")

    # 3. Static favicon / logo assets
    for asset in ["/favicon.svg?v=6", "/favicon.png?v=6", "/favicon.ico?v=6", "/logo.svg", "/logo.png"]:
        r = httpx.get(f"{base}{asset}", timeout=5.0)
        print(f"GET {asset}: status={r.status_code} (size: {len(r.content)} bytes)")
        assert r.status_code == 200, f"Asset {asset} failed with {r.status_code}"

    print("\nALL VERIFICATION CHECKS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_api()
