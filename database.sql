CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    pair_address TEXT NOT NULL,
    base_token TEXT NOT NULL,
    quote_token TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);