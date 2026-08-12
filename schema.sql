-- Enterprise CRM Database Schema (SQLite)

DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS contacts;
DROP TABLE IF EXISTS companies;
DROP TABLE IF EXISTS deals;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS pipeline_stages;
DROP TABLE IF EXISTS attachments;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'sales_rep',   -- admin, manager, sales_rep
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    industry TEXT,
    website TEXT,
    phone TEXT,
    address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    company_id INTEGER,
    job_title TEXT,
    owner_id INTEGER,               -- assigned sales rep
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (owner_id) REFERENCES users(id)
);

CREATE TABLE pipeline_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    color TEXT DEFAULT '#6366f1'
);

CREATE TABLE deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    contact_id INTEGER,
    company_id INTEGER,
    owner_id INTEGER,
    value REAL DEFAULT 0,
    stage_id INTEGER NOT NULL,
    status TEXT DEFAULT 'open',      -- open, won, lost
    expected_close_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (owner_id) REFERENCES users(id),
    FOREIGN KEY (stage_id) REFERENCES pipeline_stages(id)
);

CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,              -- call, email, meeting, task, note
    subject TEXT NOT NULL,
    description TEXT,
    due_date TIMESTAMP,
    completed INTEGER DEFAULT 0,
    contact_id INTEGER,
    deal_id INTEGER,
    owner_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (deal_id) REFERENCES deals(id),
    FOREIGN KEY (owner_id) REFERENCES users(id)
);

CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    related_type TEXT NOT NULL,     -- 'contact' or 'deal'
    related_id INTEGER NOT NULL,
    uploaded_by INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

-- Default pipeline stages
INSERT INTO pipeline_stages (name, position, color) VALUES
    ('New Lead', 1, '#94a3b8'),
    ('Contacted', 2, '#60a5fa'),
    ('Proposal Sent', 3, '#fbbf24'),
    ('Negotiation', 4, '#f97316'),
    ('Won', 5, '#22c55e'),
    ('Lost', 6, '#ef4444');

-- Default admin user (password: admin123 -- hashed at app startup / seed script)
