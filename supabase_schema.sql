-- Run this script in your Supabase SQL Editor

CREATE TABLE companies (
    company_id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    location_string VARCHAR(255),
    latitude NUMERIC(10, 8),
    longitude NUMERIC(11, 8),
    is_matrix BOOLEAN DEFAULT FALSE,
    email VARCHAR(255),
    phone VARCHAR(255),
    ai_status VARCHAR(50) DEFAULT 'pending'
);

CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    name VARCHAR(150) UNIQUE NOT NULL,
    type VARCHAR(50) NOT NULL CHECK (type IN ('MAIN', 'SUB'))
);

CREATE TABLE company_categories (
    company_id UUID,
    category_id INT,
    PRIMARY KEY (company_id, category_id),
    FOREIGN KEY (company_id) REFERENCES companies(company_id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE CASCADE
);
