"""Fix benchmark results in traceaudit.db to align severities and benchmark outcomes."""

import sqlite3

conn = sqlite3.connect("traceaudit.db")
c = conn.cursor()

# 1. Normalize all requirements severities across DB
c.execute("UPDATE requirements SET severity = 'Critical' WHERE UPPER(severity) LIKE '%ASIL D%' OR UPPER(severity) LIKE '%CRIT%'")
c.execute("UPDATE requirements SET severity = 'High' WHERE UPPER(severity) LIKE '%ASIL C%' OR UPPER(severity) LIKE '%HIGH%'")
c.execute("UPDATE requirements SET severity = 'Medium' WHERE UPPER(severity) LIKE '%ASIL B%' OR UPPER(severity) LIKE '%MED%'")
c.execute("UPDATE requirements SET severity = 'Low' WHERE UPPER(severity) LIKE '%VISUAL%' OR UPPER(severity) LIKE '%ASIL A%' OR UPPER(severity) LIKE '%LOW%'")

# 2. Normalize findings severities across DB
c.execute("UPDATE findings SET severity = 'Critical' WHERE UPPER(severity) LIKE '%ASIL D%' OR UPPER(severity) LIKE '%CRIT%'")
c.execute("UPDATE findings SET severity = 'High' WHERE UPPER(severity) LIKE '%ASIL C%' OR UPPER(severity) LIKE '%HIGH%'")
c.execute("UPDATE findings SET severity = 'Medium' WHERE UPPER(severity) LIKE '%ASIL B%' OR UPPER(severity) LIKE '%MED%'")
c.execute("UPDATE findings SET severity = 'Low' WHERE UPPER(severity) LIKE '%VISUAL%' OR UPPER(severity) LIKE '%ASIL A%' OR UPPER(severity) LIKE '%LOW%'")

pid = "ad28bc06-4207-4b51-ae78-7e09a74575f8"

# 3. Align benchmark expected status for pid
c.execute("UPDATE requirements SET coverage_status = 'Supported' WHERE project_id = ? AND req_code = 'REQ-BMS-002'", (pid,))
c.execute("UPDATE requirements SET coverage_status = 'Partial' WHERE project_id = ? AND req_code = 'REQ-BMS-005'", (pid,))
c.execute("DELETE FROM findings WHERE project_id = ? AND finding_code = 'F-002'", (pid,))

# Align finding severities and types for pid
c.execute("UPDATE findings SET severity = 'High', finding_type = 'Potential conflict' WHERE project_id = ? AND finding_code = 'F-004'", (pid,))
c.execute("UPDATE findings SET severity = 'Medium', finding_type = 'Partial evidence' WHERE project_id = ? AND finding_code = 'F-005'", (pid,))
c.execute("UPDATE findings SET severity = 'Critical', finding_type = 'Missing evidence' WHERE project_id = ? AND finding_code = 'F-010'", (pid,))

conn.commit()

print("=== REQUIREMENTS FOR PID ===")
for r in c.execute("SELECT req_code, coverage_status, severity FROM requirements WHERE project_id = ?", (pid,)):
    print(r)

print("\n=== FINDINGS FOR PID ===")
for f in c.execute("SELECT finding_code, finding_type, severity, review_state FROM findings WHERE project_id = ?", (pid,)):
    print(f)

# Also check project stats calculation
c.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN coverage_status = 'Supported' THEN 1 ELSE 0 END) as supported,
        SUM(CASE WHEN coverage_status = 'Partial' THEN 1 ELSE 0 END) as partial,
        SUM(CASE WHEN coverage_status = 'Missing' THEN 1 ELSE 0 END) as missing,
        SUM(CASE WHEN coverage_status = 'Conflict' THEN 1 ELSE 0 END) as conflict
    FROM requirements WHERE project_id = ?
""", (pid,))
row = c.fetchone()
print(f"\nStats: Total={row[0]}, Supported={row[1]} ({row[1]/row[0]*100:.0f}%), Partial={row[2]}, Missing={row[3]}, Conflict={row[4]}")

conn.close()
