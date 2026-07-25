-- Temp Mail D1 Database Schema

CREATE TABLE IF NOT EXISTS mailboxes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL DEFAULT (datetime('now', '+24 hours'))
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mailbox_address TEXT NOT NULL,
  sender TEXT,
  subject TEXT,
  text_body TEXT,
  html_body TEXT,
  raw_headers TEXT,
  received_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (mailbox_address) REFERENCES mailboxes(address)
);

CREATE INDEX IF NOT EXISTS idx_messages_mailbox ON messages(mailbox_address);
CREATE INDEX IF NOT EXISTS idx_mailboxes_expires ON mailboxes(expires_at);
CREATE INDEX IF NOT EXISTS idx_mailboxes_address ON mailboxes(address);
