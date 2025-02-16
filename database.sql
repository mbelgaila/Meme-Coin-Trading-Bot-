-- Switch to postgres user first
\c postgres;

-- Create the database
CREATE DATABASE meme_bot;

-- Connect to the database
\c meme_bot;

-- Create trades table
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    pair_address TEXT NOT NULL,
    base_token TEXT NOT NULL,
    quote_token TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Grant privileges to mohamed
GRANT ALL PRIVILEGES ON DATABASE meme_bot TO mohamed;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO mohamed;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO mohamed;
