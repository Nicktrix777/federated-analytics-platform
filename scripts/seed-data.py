#!/usr/bin/env python3
"""
seed_data.py  ─  Federated Analytics Platform Data Seeder
==========================================================
Populates postgres-source (PostgreSQL) and mongo-source (MongoDB)
with realistic employee-management and task data for demonstrating
cross-data-source queries via Trino.

SCHEMA OVERVIEW
───────────────
PostgreSQL  →  source_db (port 5433)
  ├── departments         8 business units with cost centres and locations
  ├── employees           80 staff with salaries, titles, hire dates, managers
  └── performance_reviews 160 quarterly review records (Q1-Q4 2024)

MongoDB  →  employee_db  (port 27017)
  ├── tasks               ~500 work items (status, priority, due dates, tags)
  ├── employee_profiles   rich profiles: skills, certs, education, preferences
  └── _schema             Trino schema definitions for accurate type inference

Does NOT touch analytics_meta / postgres-meta: datasets, dataset_columns and
table_relationships are discovered live from Trino via Core API's catalog sync
("Sync Catalogs" in the UI / POST /api/datasources/sync), so this script only
needs to land rows in the two source databases for that sync to pick up.

CROSS-SOURCE JOIN KEY
─────────────────────
  employees.employee_id  (INTEGER)
      ←→  tasks.employee_id          (INTEGER in MongoDB BSON Int32)
      ←→  employee_profiles.employee_id  (INTEGER in MongoDB BSON Int32)

USAGE
─────
  pip install psycopg2-binary pymongo faker

  # defaults match docker-compose ports exactly:
  python seed_data.py

  # override any connection param:
  PG_HOST=localhost PG_PORT=5433 MONGO_HOST=localhost python seed_data.py

  # scale up for load testing:
  NUM_EMPLOYEES=200 NUM_TASKS=2000 python seed_data.py
"""

from __future__ import annotations

import os
import sys
import random
import datetime
import time
from decimal import Decimal
from typing import List, Dict, Any, Tuple

# ─── Dependency guard ──────────────────────────────────────────────────────────
try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    sys.exit("ERROR: pip install psycopg2-binary")

try:
    from pymongo import MongoClient, ASCENDING
except ImportError:
    sys.exit("ERROR: pip install pymongo")

try:
    from faker import Faker
except ImportError:
    sys.exit("ERROR: pip install faker")


# ─── Reproducibility ──────────────────────────────────────────────────────────
SEED = 42
random.seed(SEED)
fake = Faker("en_IN")
fake.seed_instance(SEED)


# ─── Connection config (mirrors docker-compose defaults) ──────────────────────
PG_SOURCE_CONFIG: Dict[str, Any] = dict(
    host            = os.getenv("PG_HOST",      "localhost"),
    port            = int(os.getenv("PG_PORT",  "5433")),
    dbname          = os.getenv("PG_DB",        "source_db"),
    user            = os.getenv("PG_USER",      "source_user"),
    password        = os.getenv("PG_PASS",      "source_pass_2024"),
    connect_timeout = 10,
)
MONGO_HOST    = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT    = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB_NAME = os.getenv("MONGO_DB",   "employee_db")
MONGO_URI     = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"

NUM_EMPLOYEES: int = int(os.getenv("NUM_EMPLOYEES", "80"))
NUM_TASKS: int     = int(os.getenv("NUM_TASKS",     "500"))


# ═══════════════════════════════════════════════════════════════════════════════
#  STATIC REFERENCE DATA
# ═══════════════════════════════════════════════════════════════════════════════

DEPARTMENTS: List[Tuple[str, str, str, str]] = [
    # (name, cost_center, location, description)
    ("Engineering",      "ENG-001", "Bangalore", "Builds and maintains all product engineering systems"),
    ("Product",          "PRD-001", "Bangalore", "Drives product vision, roadmap, and cross-functional alignment"),
    ("Sales",            "SAL-001", "Mumbai",    "Manages enterprise and mid-market sales pipeline"),
    ("Marketing",        "MKT-001", "Mumbai",    "Brand, growth, demand generation, and content"),
    ("Operations",       "OPS-001", "Delhi",     "Process excellence, scrum facilitation, and program management"),
    ("Human Resources",  "HR-001",  "Bangalore", "Talent acquisition, culture, and people operations"),
    ("Finance",          "FIN-001", "Delhi",     "Accounting, FP&A, and financial compliance"),
    ("Customer Success", "CS-001",  "Hyderabad", "Customer onboarding, retention, and health"),
]

TITLES_BY_DEPT: Dict[str, List[str]] = {
    "Engineering": [
        "Junior Software Engineer", "Software Engineer", "Senior Software Engineer",
        "Staff Engineer", "Engineering Manager", "DevOps Engineer",
        "QA Engineer", "Backend Engineer", "Frontend Engineer",
    ],
    "Product": [
        "Associate Product Manager", "Product Manager", "Senior Product Manager",
        "Principal Product Manager", "Head of Product", "Product Analyst",
    ],
    "Sales": [
        "Sales Development Representative", "Account Executive", "Senior Account Executive",
        "Sales Manager", "Regional Sales Director", "VP of Sales",
    ],
    "Marketing": [
        "Marketing Associate", "Content Specialist", "SEO Analyst",
        "Growth Marketer", "Marketing Manager", "Head of Marketing",
    ],
    "Operations": [
        "Operations Analyst", "Scrum Master", "Program Manager",
        "Operations Manager", "Senior Program Manager", "VP of Operations",
    ],
    "Human Resources": [
        "HR Coordinator", "Recruiter", "HR Business Partner",
        "Compensation Analyst", "People Operations Manager", "HR Director",
    ],
    "Finance": [
        "Finance Analyst", "Accountant", "Senior Accountant",
        "Finance Manager", "Financial Controller", "CFO",
    ],
    "Customer Success": [
        "Customer Success Associate", "Customer Success Manager", "Senior CSM",
        "Onboarding Specialist", "CS Team Lead", "VP of Customer Success",
    ],
}

# Annual salary ranges in INR
SALARY_RANGES: Dict[str, Tuple[int, int]] = {
    "Engineering":      (700_000,  3_500_000),
    "Product":          (900_000,  4_000_000),
    "Sales":            (600_000,  3_000_000),
    "Marketing":        (500_000,  2_800_000),
    "Operations":       (600_000,  2_500_000),
    "Human Resources":  (500_000,  2_200_000),
    "Finance":          (700_000,  3_000_000),
    "Customer Success": (550_000,  2_600_000),
}

SKILLS_BY_DEPT: Dict[str, List[str]] = {
    "Engineering": [
        "Python", "Go", "Java", "TypeScript", "React", "PostgreSQL",
        "MongoDB", "Redis", "Kubernetes", "Docker", "AWS", "GCP",
        "Terraform", "GraphQL", "gRPC", "Kafka", "Elasticsearch",
    ],
    "Product": [
        "Figma", "JIRA", "Confluence", "Data Analysis", "User Research",
        "A/B Testing", "Roadmapping", "Wireframing", "SQL", "Mixpanel",
    ],
    "Sales": [
        "Salesforce", "HubSpot", "CRM", "Cold Outreach", "Negotiation",
        "LinkedIn Sales Navigator", "Forecasting", "Account Planning",
    ],
    "Marketing": [
        "Google Analytics", "HubSpot", "Canva", "Copywriting", "SEO",
        "Social Media", "Email Marketing", "Google Ads", "Meta Ads",
    ],
    "Operations": [
        "JIRA", "Asana", "Confluence", "Process Mapping", "Six Sigma",
        "Agile", "Scrum", "OKR Frameworks", "Tableau",
    ],
    "Human Resources": [
        "Workday", "BambooHR", "Conflict Resolution", "Performance Management",
        "Recruiting", "Compensation Benchmarking", "HRIS", "D&I Programs",
    ],
    "Finance": [
        "Excel", "QuickBooks", "Financial Modelling", "GAAP", "SAP",
        "Power BI", "Budgeting", "Variance Analysis", "NetSuite",
    ],
    "Customer Success": [
        "Zendesk", "Intercom", "NPS", "Churn Analysis", "Customer Journeys",
        "SQL", "Data Studio", "Salesforce Service Cloud",
    ],
}

PROJECTS: List[str] = [
    "Platform Revamp Q1",      "Mobile App Launch",
    "Data Infrastructure",     "Customer Portal v2",
    "Security Audit",          "Onboarding Automation",
    "Sales Pipeline Overhaul", "Q2 Marketing Campaign",
    "Internal Tooling Sprint", "API Gateway Migration",
    "GDPR Compliance",         "BI Dashboard Rollout",
    "Year-End Reporting",      "Payroll System Upgrade",
    "Accessibility Initiative",
]

CERTIFICATIONS: Dict[str, List[Tuple[str, str]]] = {
    "Engineering": [
        ("AWS Certified Solutions Architect", "Amazon Web Services"),
        ("Certified Kubernetes Administrator", "CNCF"),
        ("Google Cloud Professional", "Google"),
        ("HashiCorp Terraform Associate", "HashiCorp"),
    ],
    "Product": [
        ("Certified Product Manager", "AIPMM"),
        ("Pragmatic Marketing Certified", "Pragmatic Institute"),
        ("Google Analytics Certified", "Google"),
    ],
    "Sales": [
        ("Salesforce Certified Sales Cloud", "Salesforce"),
        ("Challenger Sales Certification", "Gartner"),
    ],
    "Marketing": [
        ("HubSpot Marketing Certification", "HubSpot Academy"),
        ("Google Ads Certification", "Google"),
        ("SEMrush SEO Toolkit Certification", "SEMrush"),
    ],
    "Operations": [
        ("PMP - Project Management Professional", "PMI"),
        ("Certified Scrum Master", "Scrum Alliance"),
        ("Lean Six Sigma Green Belt", "ASQ"),
    ],
    "Human Resources": [
        ("SHRM-CP", "SHRM"),
        ("PHR Certification", "HRCI"),
    ],
    "Finance": [
        ("CA - Chartered Accountant", "ICAI"),
        ("CMA - Cost Management Accountant", "ICMAI"),
        ("CFA Level 1", "CFA Institute"),
    ],
    "Customer Success": [
        ("Certified Customer Success Manager", "SuccessHACKER"),
        ("Gainsight Certification", "Gainsight"),
    ],
}

DEGREES = ["B.Tech", "M.Tech", "MBA", "BBA", "B.Sc", "M.Sc", "B.Com", "M.Com"]
INSTITUTIONS = [
    "IIT Bombay", "IIT Delhi", "IIT Madras", "IIM Ahmedabad",
    "IIM Bangalore", "BITS Pilani", "NIT Trichy", "VIT Vellore",
    "Pune University", "Delhi University", "XLRI Jamshedpur", "ISB Hyderabad",
]

STATUSES_EMP    = ["active", "active", "active", "active", "active", "on_leave", "inactive"]
TASK_STATUSES   = ["open", "in_progress", "completed", "blocked"]
TASK_STATUS_W   = [0.20, 0.30, 0.40, 0.10]
TASK_PRIORITIES = ["low", "medium", "high", "critical"]
TASK_PRIORITY_W = [0.20, 0.40, 0.30, 0.10]

REVIEW_PERIODS = ["Q1-2024", "Q2-2024", "Q3-2024", "Q4-2024"]

# Task title templates with fill-in slots
_TASK_TEMPLATES = [
    ("Implement {feature} feature",                   "Engineering"),
    ("Code review: {module} pull request",            "Engineering"),
    ("Write unit tests for {module}",                 "Engineering"),
    ("Fix critical bug in {component}",               "Engineering"),
    ("Deploy {service} to staging environment",       "Engineering"),
    ("Migrate {module} to new architecture",          "Engineering"),
    ("Performance profiling for {component}",         "Engineering"),
    ("Document {module} API endpoints",               "Engineering"),
    ("Draft product spec for {feature}",              "Product"),
    ("Conduct user interviews for {module}",          "Product"),
    ("Prioritise backlog for Q{q} sprint",            "Product"),
    ("Create A/B test plan for {feature}",            "Product"),
    ("Update OKRs for {module} squad",                "Product"),
    ("Analyse funnel drop-off in {component}",        "Product"),
    ("Reach out to {count} new enterprise leads",     "Sales"),
    ("Follow up on {module} proposal",                "Sales"),
    ("Prepare sales deck for {feature}",              "Sales"),
    ("Update CRM pipeline for {module}",              "Sales"),
    ("Negotiate contract renewal: {component}",       "Sales"),
    ("Write blog post on {feature}",                  "Marketing"),
    ("Launch social campaign for {module} release",   "Marketing"),
    ("Analyse SEO for {component} landing page",      "Marketing"),
    ("Create newsletter for Q{q} product update",     "Marketing"),
    ("Run paid ads experiment: {feature}",            "Marketing"),
    ("Document {feature} runbook",                    "Operations"),
    ("Run retro for {module} sprint",                 "Operations"),
    ("Schedule all-hands for Q{q} OKR review",       "Operations"),
    ("Review SLA compliance for {component}",         "Operations"),
    ("Update Confluence: {module} processes",         "Operations"),
    ("Post {count} open job listings",                "Human Resources"),
    ("Complete Q{q} performance review cycle",        "Human Resources"),
    ("Update employee handbook: {module}",            "Human Resources"),
    ("Onboard {count} new hires",                     "Human Resources"),
    ("Run engagement survey for {feature} team",      "Human Resources"),
    ("Reconcile {module} expense reports",            "Finance"),
    ("Prepare Q{q} budget variance report",           "Finance"),
    ("Close month-end books: {component}",            "Finance"),
    ("Review vendor contracts ({count} vendors)",     "Finance"),
    ("Update revenue forecast model Q{q}",            "Finance"),
    ("QBR preparation for {module} accounts",         "Customer Success"),
    ("Resolve escalation: {component} client",        "Customer Success"),
    ("Create onboarding guide for {feature}",         "Customer Success"),
    ("Conduct NPS survey for {module} segment",       "Customer Success"),
    ("Renew contract: {component} enterprise account","Customer Success"),
]

_FILL: Dict[str, List[str]] = {
    "feature":   ["authentication", "reporting", "billing", "notifications", "analytics",
                  "search", "export", "dashboards", "integrations", "RBAC"],
    "module":    ["user management", "order processing", "data pipeline", "payment gateway",
                  "audit log", "recommendation engine", "employee directory", "scheduler"],
    "component": ["API gateway", "frontend", "backend service", "cache layer", "message queue"],
    "service":   ["auth-service", "core-api", "query-engine", "data-ingestion", "scheduler"],
    "count":     ["5", "10", "15", "20", "3", "7", "12"],
    "q":         ["1", "2", "3", "4"],
}


def _fill(template: str) -> str:
    for key, options in _FILL.items():
        if f"{{{key}}}" in template:
            template = template.replace(f"{{{key}}}", random.choice(options))
    return template


def _rand_date(start: datetime.date, end: datetime.date) -> datetime.date:
    delta = (end - start).days
    return start + datetime.timedelta(days=random.randint(0, delta))


def _rand_dt(start: datetime.date, end: datetime.date) -> datetime.datetime:
    d = _rand_date(start, end)
    return datetime.datetime(d.year, d.month, d.day,
                             random.randint(8, 19), random.randint(0, 59))


# Seniority level 1 (most junior) … 5 (executive), inferred from the job title.
# Drives both salary placement and the reporting hierarchy so "avg salary by
# level" and org-chart queries are meaningful rather than random.
def _seniority_level(title: str) -> int:
    t = title.lower()
    if any(k in t for k in ("cfo", "chief", "vp", "vice president", "head of", "director", "controller")):
        return 5
    if any(k in t for k in ("principal", "staff", "manager", "lead", "team lead")):
        return 4
    if "senior" in t or t.startswith("sr"):
        return 3
    if any(k in t for k in ("junior", "associate", "coordinator", "representative", "sdr", "analyst", "accountant", "recruiter", "specialist")):
        return 1
    return 2


def _salary_for_level(level: int, lo: int, hi: int) -> int:
    """Place salary within [lo, hi] according to seniority level (1..5), with
    modest jitter, so senior titles reliably out-earn junior ones."""
    frac = (level - 1) / 4.0
    span = hi - lo
    center = lo + frac * span
    sal = center + random.uniform(-0.10, 0.10) * span
    sal = max(lo, min(hi, sal))
    return int(round(sal / 1000) * 1000)


# ═══════════════════════════════════════════════════════════════════════════════
#  POSTGRESQL  ─  SOURCE DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def seed_postgres(conn) -> List[Dict[str, Any]]:
    """Create schema and seed departments, employees, performance_reviews.
    Returns list of employee dicts for cross-seeding into MongoDB."""
    cur = conn.cursor()

    print("  [PG] Dropping and recreating tables …")

    cur.execute("""
        DROP TABLE IF EXISTS performance_reviews CASCADE;
        DROP TABLE IF EXISTS employees CASCADE;
        DROP TABLE IF EXISTS departments CASCADE;
    """)

    # ── Departments ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE departments (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(100) NOT NULL UNIQUE,
            cost_center VARCHAR(20)  NOT NULL,
            location    VARCHAR(100) NOT NULL,
            description TEXT,
            headcount   INTEGER DEFAULT 0,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    dept_rows = [
        (name, cc, loc, desc)
        for name, cc, loc, desc in DEPARTMENTS
    ]
    execute_values(cur,
        "INSERT INTO departments (name, cost_center, location, description) "
        "VALUES %s RETURNING id, name",
        dept_rows)
    rows = cur.fetchall()
    dept_id_map: Dict[str, int] = {name: did for did, name in rows}
    print(f"  [PG] Inserted {len(dept_rows)} departments")

    # ── Employees ────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE employees (
            employee_id  SERIAL PRIMARY KEY,
            first_name   VARCHAR(100) NOT NULL,
            last_name    VARCHAR(100) NOT NULL,
            email        VARCHAR(200) NOT NULL UNIQUE,
            department_id INTEGER NOT NULL REFERENCES departments(id),
            department   VARCHAR(100) NOT NULL,
            job_title    VARCHAR(150) NOT NULL,
            hire_date    DATE         NOT NULL,
            salary       NUMERIC(12,2) NOT NULL,
            status       VARCHAR(20)  NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','inactive','on_leave')),
            manager_id   INTEGER REFERENCES employees(employee_id),
            phone        VARCHAR(20),
            location     VARCHAR(100) NOT NULL,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    hire_start = datetime.date(2019, 1, 1)
    hire_end   = datetime.date(2024, 12, 31)

    dept_names = [d[0] for d in DEPARTMENTS]
    per_dept   = max(1, NUM_EMPLOYEES // len(dept_names))
    remainder  = NUM_EMPLOYEES - per_dept * len(dept_names)

    employees: List[Dict[str, Any]] = []
    dept_employee_map: Dict[str, List[int]] = {d: [] for d in dept_names}

    for i, dept_name in enumerate(dept_names):
        count = per_dept + (1 if i < remainder else 0)
        dept_loc = next(loc for name, _, loc, _ in DEPARTMENTS if name == dept_name)
        titles   = TITLES_BY_DEPT[dept_name]
        sal_lo, sal_hi = SALARY_RANGES[dept_name]

        for _ in range(count):
            fn    = fake.first_name()
            ln    = fake.last_name()
            email = f"{fn.lower()}.{ln.lower()}{random.randint(1,99)}@acme-corp.in"
            title = random.choice(titles)
            level = _seniority_level(title)
            sal   = _salary_for_level(level, sal_lo, sal_hi)
            hdate = _rand_date(hire_start, hire_end)
            st    = random.choice(STATUSES_EMP)
            phone = fake.phone_number()

            employees.append({
                "first_name":    fn,
                "last_name":     ln,
                "email":         email,
                "department_id": dept_id_map[dept_name],
                "department":    dept_name,
                "job_title":     title,
                "level":         level,
                "hire_date":     hdate,
                "salary":        sal,
                "status":        st,
                "phone":         phone,
                "location":      dept_loc,
                "manager_id":    None,   # filled in once the hierarchy is built
            })

    # Insert employees in one batch (no manager_id yet)
    emp_insert_data = [
        (e["first_name"], e["last_name"], e["email"],
         e["department_id"], e["department"], e["job_title"],
         e["hire_date"], e["salary"], e["status"],
         e["phone"], e["location"])
        for e in employees
    ]
    execute_values(cur, """
        INSERT INTO employees
            (first_name, last_name, email, department_id, department,
             job_title, hire_date, salary, status, phone, location)
        VALUES %s
        RETURNING employee_id, department
    """, emp_insert_data)
    emp_rows = cur.fetchall()

    # Map employee_id back into our employee dicts
    for idx, (eid, dept) in enumerate(emp_rows):
        employees[idx]["employee_id"] = eid
        dept_employee_map[dept].append(eid)

    # ── Reporting hierarchy ──────────────────────────────────────────────────
    # Per department: the most-senior person is the department head (no
    # manager); a tier of managers reports to the head; individual
    # contributors are spread across those managers. This yields a real
    # multi-level tree instead of "first employee manages everyone", so
    # org-chart / span-of-control queries return something meaningful.
    emp_by_id: Dict[int, Dict[str, Any]] = {e["employee_id"]: e for e in employees}
    dept_heads: List[int] = []
    for dept_name, eids in dept_employee_map.items():
        if not eids:
            continue
        members = sorted(eids, key=lambda x: emp_by_id[x]["level"], reverse=True)
        head = members[0]
        emp_by_id[head]["manager_id"] = None
        dept_heads.append(head)

        rest = members[1:]
        if not rest:
            continue
        n_managers = max(1, len(rest) // 5)
        managers = rest[:n_managers]
        ics      = rest[n_managers:]
        for m in managers:
            emp_by_id[m]["manager_id"] = head
        for i, ic in enumerate(ics):
            emp_by_id[ic]["manager_id"] = managers[i % len(managers)]

    for e in employees:
        cur.execute("UPDATE employees SET manager_id = %s WHERE employee_id = %s",
                    (e["manager_id"], e["employee_id"]))

    print(f"  [PG] Inserted {len(employees)} employees "
          f"({len(dept_heads)} department heads)")

    # Update department headcounts
    cur.execute("""
        UPDATE departments d
        SET headcount = sub.cnt
        FROM (SELECT department_id, COUNT(*) as cnt FROM employees GROUP BY 1) sub
        WHERE d.id = sub.department_id
    """)

    # ── Performance Reviews ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE performance_reviews (
            review_id     SERIAL PRIMARY KEY,
            employee_id   INTEGER NOT NULL REFERENCES employees(employee_id),
            reviewer_id   INTEGER NOT NULL REFERENCES employees(employee_id),
            review_period VARCHAR(20) NOT NULL,
            review_date   DATE NOT NULL,
            score         NUMERIC(3,1) NOT NULL CHECK (score BETWEEN 1.0 AND 5.0),
            goals_met     BOOLEAN NOT NULL,
            strengths     TEXT,
            areas_to_improve TEXT,
            created_at    TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    review_records = []
    period_dates = {
        "Q1-2024": datetime.date(2024, 3, 31),
        "Q2-2024": datetime.date(2024, 6, 30),
        "Q3-2024": datetime.date(2024, 9, 30),
        "Q4-2024": datetime.date(2024, 12, 31),
    }

    strengths_pool = [
        "Delivers consistently high-quality work", "Strong team collaborator",
        "Excellent communication skills", "Proactively identifies problems",
        "Technical depth in core domain", "Customer-centric mindset",
        "Meets deadlines reliably", "Drives process improvements",
    ]
    improve_pool = [
        "Can improve delegation skills", "Documentation could be more thorough",
        "Should speak up more in cross-team meetings", "Time estimation accuracy",
        "Needs to focus on strategic thinking", "Could mentor juniors more",
    ]

    # Every employee gets all four quarters of 2024 with a per-person score
    # trajectory (a base level plus a gentle upward/downward slope and small
    # noise), so quarter-over-quarter trend charts show real movement instead
    # of independent random points.
    for emp in employees:
        eid = emp["employee_id"]
        # Reviewer is the employee's manager; department heads (no manager) are
        # reviewed by another department head to keep reviewer_id a valid FK
        # without self-reviews.
        manager = emp["manager_id"]
        if manager is None:
            others  = [h for h in dept_heads if h != eid]
            manager = random.choice(others) if others else eid

        base  = random.gauss(3.3, 0.5)
        slope = random.uniform(-0.25, 0.35)

        for qi, period in enumerate(REVIEW_PERIODS):
            score     = base + slope * qi + random.gauss(0, 0.15)
            score     = round(max(1.0, min(5.0, score)), 1)
            goals_met = score >= 3.5
            rdate     = period_dates[period] + datetime.timedelta(days=random.randint(-7, 7))

            review_records.append((
                eid, manager, period, rdate,
                score, goals_met,
                random.choice(strengths_pool), random.choice(improve_pool),
            ))

    execute_values(cur, """
        INSERT INTO performance_reviews
            (employee_id, reviewer_id, review_period, review_date,
             score, goals_met, strengths, areas_to_improve)
        VALUES %s
    """, review_records)
    print(f"  [PG] Inserted {len(review_records)} performance reviews")

    conn.commit()
    cur.close()
    return employees


# ═══════════════════════════════════════════════════════════════════════════════
#  MONGODB  ─  SOURCE DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def seed_mongodb(db, employees: List[Dict[str, Any]]) -> None:
    """Create tasks and employee_profiles collections with realistic data."""

    db.drop_collection("tasks")
    db.drop_collection("employee_profiles")
    db.drop_collection("_schema")

    all_eids  = [e["employee_id"]  for e in employees]
    dept_map  = {e["employee_id"]: e["department"] for e in employees}

    # ── Trino _schema collection ─────────────────────────────────────────────
    # Explicit schema prevents Trino from falling back to sampling,
    # which can infer wrong types when optional fields are absent in early docs.
    db["_schema"].insert_many([
        {
            "table": "tasks",
            "fields": [
                {"name": "task_id",        "type": "varchar",   "hidden": False},
                {"name": "title",          "type": "varchar",   "hidden": False},
                {"name": "description",    "type": "varchar",   "hidden": False},
                {"name": "status",         "type": "varchar",   "hidden": False},
                {"name": "priority",       "type": "varchar",   "hidden": False},
                {"name": "employee_id",    "type": "integer",   "hidden": False},
                {"name": "created_by",     "type": "integer",   "hidden": False},
                {"name": "project",        "type": "varchar",   "hidden": False},
                {"name": "tags",           "type": "array(varchar)", "hidden": False},
                {"name": "due_date",       "type": "timestamp", "hidden": False},
                {"name": "created_at",     "type": "timestamp", "hidden": False},
                {"name": "completed_at",   "type": "timestamp", "hidden": True},
                {"name": "estimated_hours","type": "double",    "hidden": False},
                {"name": "actual_hours",   "type": "double",    "hidden": False},
            ]
        },
        {
            "table": "employee_profiles",
            "fields": [
                # integer (not varchar) — the documents store int(employee_id)
                # and it JOINs to postgres employees.employee_id (INTEGER); a
                # varchar declaration here forced a type mismatch on that join.
                {"name": "employee_id",            "type": "integer",        "hidden": False},
                {"name": "bio",                    "type": "varchar",        "hidden": False},
                {"name": "skills",                 "type": "array(varchar)", "hidden": False},
                {"name": "remote_preference",      "type": "varchar",        "hidden": False},
                {"name": "working_hours",          "type": "varchar",        "hidden": False},
                {"name": "communication_style",    "type": "varchar",        "hidden": False},
                {"name": "linkedin_url",           "type": "varchar",        "hidden": False},
                {"name": "created_at",             "type": "timestamp",      "hidden": False},
                {"name": "updated_at",             "type": "timestamp",      "hidden": False},
            ]
        }
    ])
    print("  [MG] Registered _schema collection for Trino type inference")

    # ── Tasks ─────────────────────────────────────────────────────────────────
    # Build templates scoped per department
    dept_templates: Dict[str, List[str]] = {d: [] for d in [r[0] for r in DEPARTMENTS]}
    all_templates: List[str] = []
    for tpl, dept in _TASK_TEMPLATES:
        if dept in dept_templates:
            dept_templates[dept].append(tpl)
        all_templates.append(tpl)

    today      = datetime.date.today()
    date_start = datetime.date(2024, 1, 1)
    date_end   = today + datetime.timedelta(days=90)

    tasks = []
    for i in range(NUM_TASKS):
        emp_id   = random.choice(all_eids)
        dept     = dept_map[emp_id]
        tpl_pool = dept_templates.get(dept, all_templates) or all_templates
        title    = _fill(random.choice(tpl_pool))

        status   = random.choices(TASK_STATUSES, weights=TASK_STATUS_W)[0]
        priority = random.choices(TASK_PRIORITIES, weights=TASK_PRIORITY_W)[0]
        project  = random.choice(PROJECTS)

        created_dt  = _rand_dt(date_start, today)
        due_date_dt = datetime.datetime(
            *(today + datetime.timedelta(days=random.randint(-30, 60))).timetuple()[:6]
        )

        completed_at = None
        actual_hours = None
        est_hours    = round(random.uniform(1, 20), 1)

        if status == "completed":
            completed_at = created_dt + datetime.timedelta(
                hours=random.randint(2, int(est_hours * 24))
            )
            actual_hours = round(est_hours * random.uniform(0.6, 1.8), 1)

        # Dept-specific tags
        tag_pool = {
            "Engineering": ["backend", "frontend", "infra", "bug", "feature", "perf"],
            "Product":     ["roadmap", "research", "spec", "design", "analytics"],
            "Sales":       ["enterprise", "smb", "pipeline", "renewal", "cold-outreach"],
            "Marketing":   ["content", "seo", "paid", "social", "email"],
            "Operations":  ["process", "agile", "okr", "compliance", "tooling"],
            "Human Resources": ["hiring", "culture", "benefits", "onboarding", "l&d"],
            "Finance":     ["reporting", "compliance", "audit", "budget", "forecast"],
            "Customer Success": ["retention", "health", "onboarding", "escalation", "nps"],
        }
        tags = random.sample(tag_pool.get(dept, ["general"]), k=random.randint(1, 3))

        tasks.append({
            "task_id":         f"TASK-{i + 1:05d}",
            "title":           title,
            "description":     fake.sentence(nb_words=12),
            "status":          status,
            "priority":        priority,
            "employee_id":     int(emp_id),           # BSON Int32 — matches PG INTEGER
            "created_by":      int(random.choice(all_eids)),
            "project":         random.choice(PROJECTS),
            "tags":            tags,
            "due_date":        due_date_dt,
            "created_at":      created_dt,
            "completed_at":    completed_at,
            "estimated_hours": est_hours,
            "actual_hours":    actual_hours,
        })

    db["tasks"].insert_many(tasks)
    db["tasks"].create_index([("employee_id", ASCENDING)])
    db["tasks"].create_index([("status", ASCENDING)])
    db["tasks"].create_index([("project", ASCENDING)])
    print(f"  [MG] Inserted {len(tasks)} tasks")

    # ── Employee Profiles ────────────────────────────────────────────────────
    profiles = []
    remote_opts     = ["fully_remote", "hybrid_2_days", "hybrid_3_days", "on_site"]
    hours_opts      = ["9am-6pm IST", "10am-7pm IST", "flexible", "8am-5pm IST"]
    comm_opts       = ["async-first", "sync-heavy", "balanced", "deep-work blocks"]

    for emp in employees:
        eid  = emp["employee_id"]
        dept = emp["department"]
        now  = datetime.datetime.utcnow()

        skills_pool = SKILLS_BY_DEPT.get(dept, [])
        skills = random.sample(skills_pool, k=min(random.randint(3, 7), len(skills_pool)))

        certs_pool = CERTIFICATIONS.get(dept, [])
        certs = [
            {"name": name, "issuer": issuer, "year": random.randint(2019, 2024)}
            for name, issuer in random.sample(certs_pool, k=min(random.randint(0, 2), len(certs_pool)))
        ]

        edu_count = random.randint(1, 2)
        education = [
            {
                "degree":      random.choice(DEGREES),
                "institution": random.choice(INSTITUTIONS),
                "year":        random.randint(2010, 2022),
            }
            for _ in range(edu_count)
        ]

        profiles.append({
            "employee_id":         int(eid),
            "bio":                 fake.paragraph(nb_sentences=3),
            "skills":              skills,
            "certifications":      certs,
            "education":           education,
            "remote_preference":   random.choice(remote_opts),
            "working_hours":       random.choice(hours_opts),
            "communication_style": random.choice(comm_opts),
            "linkedin_url":        f"https://linkedin.com/in/{emp['first_name'].lower()}-{emp['last_name'].lower()}-{eid}",
            "emergency_contact": {
                "name":         fake.name(),
                "relationship": random.choice(["Spouse", "Parent", "Sibling"]),
                "phone":        fake.phone_number(),
            },
            "created_at":  datetime.datetime(emp["hire_date"].year, emp["hire_date"].month, emp["hire_date"].day),
            "updated_at":  now,
        })

    db["employee_profiles"].insert_many(profiles)
    db["employee_profiles"].create_index([("employee_id", ASCENDING)], unique=True)
    print(f"  [MG] Inserted {len(profiles)} employee profiles")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _banner(msg: str) -> None:
    print(f"\n{'─'*58}")
    print(f"  {msg}")
    print(f"{'─'*58}")


def main() -> None:
    total_start = time.perf_counter()

    print("""
╔══════════════════════════════════════════════════════════╗
║   Federated Analytics Platform — Data Seeder            ║
║   Employee Management + Tasks  (cross-source demo)      ║
╚══════════════════════════════════════════════════════════╝
""")

    # ── PostgreSQL source ────────────────────────────────────────────────────
    _banner("1/2  PostgreSQL Source  →  source_db")
    print(f"     host={PG_SOURCE_CONFIG['host']}:{PG_SOURCE_CONFIG['port']}")
    try:
        pg_conn = psycopg2.connect(**PG_SOURCE_CONFIG)
    except Exception as e:
        sys.exit(f"\nERROR connecting to postgres-source: {e}\n"
                 f"Is the stack running?  docker compose up -d")

    t0 = time.perf_counter()
    employees = seed_postgres(pg_conn)
    pg_conn.close()
    print(f"  [PG] Done  ({time.perf_counter() - t0:.1f}s)")

    # ── MongoDB source ───────────────────────────────────────────────────────
    _banner("2/2  MongoDB Source  →  employee_db")
    print(f"     uri={MONGO_URI}")
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
        mongo_client.admin.command("ping")  # fail fast if unreachable
    except Exception as e:
        sys.exit(f"\nERROR connecting to mongo-source: {e}")

    t0 = time.perf_counter()
    seed_mongodb(mongo_client[MONGO_DB_NAME], employees)
    mongo_client.close()
    print(f"  [MG] Done  ({time.perf_counter() - t0:.1f}s)")

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = time.perf_counter() - total_start
    depts   = len(DEPARTMENTS)
    reviews = len(employees) * 2

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  Seeding complete in {elapsed:5.1f}s                           ║
╠══════════════════════════════════════════════════════════╣
║  PostgreSQL (source_db)                                  ║
║    departments        : {depts:<5}                            ║
║    employees          : {len(employees):<5}                            ║
║    performance_reviews: {reviews:<5}                            ║
║                                                          ║
║  MongoDB (employee_db)                                   ║
║    tasks              : {NUM_TASKS:<5}                            ║
║    employee_profiles  : {len(employees):<5}                            ║
╠══════════════════════════════════════════════════════════╣
║  Cross-source join key:                                  ║
║  employees.employee_id ←→ tasks.employee_id              ║
║  employees.employee_id ←→ employee_profiles.employee_id  ║
╚══════════════════════════════════════════════════════════╝

  → Source data is seeded, but NOT yet registered with the AI Engine.
    Open http://localhost:3000, go to Data Sources, and click 'Sync Catalogs'
    to discover these tables via Trino and register datasets/schemas/joins.
  → Run demo queries from  demo_queries.sql
  → Ask the AI natural language questions at  http://localhost:3000

  Example AI prompts to try:
    "Show total tasks per department with completion rate"
    "Which employees have the highest performance scores and most overdue tasks?"
    "List Engineering employees hired in 2023 and their open task count"
    "What is the average salary per department?"
    "Show employees who prefer remote work and their task completion rate"
""")


if __name__ == "__main__":
    main()