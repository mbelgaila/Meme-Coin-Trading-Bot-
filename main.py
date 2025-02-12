import os
import time
import requests
import asyncio
import websockets
import pandas as pd
import psycopg2
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.publickey import PublicKey
from solana.system_program import TransferParams, transfer
from solana.rpc.types import TxOpts
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DEX_SCREENER_API = os.getenv("DEX_SCREENER_API_KEY")
JUPITER_API = os.getenv("JUPITER_API_KEY")
SOLANA_RPC = os.getenv("SOLANA_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME"))
MAX_CONTRACT_AGE = int(os.getenv("MAX_CONTRACT_AGE"))
PROFIT_TARGET = float(os.getenv("PROFIT_TARGET"))
STOP_LOSS = float(os.getenv("STOP_LOSS"))

# Database connection
conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD")
)
cursor = conn.cursor()

# Solana client
solana_client = Client(SOLANA_RPC)

# Fetch new pairs from DEX Screener
def fetch_pairs():
    url = "https://api.dexscreener.com/latest/dex/chains/solana"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json().get("pairs", [])
    return []

# Apply trading filters
def apply_filters(pair):
    liquidity = float(pair.get("liquidity", {}).get("usd", 0))
    volume = float(pair.get("volume", {}).get("h24", 0))
    created_at = pair.get("pairCreatedAt", 0)
    age = time.time() - (created_at / 1000)  # Convert to seconds
    return liquidity >= MIN_LIQUIDITY and volume >= MIN_VOLUME and age <= MAX_CONTRACT_AGE

# Get swap quote from Jupiter API
def get_swap_quote(token_in, token_out, amount):
    url = f"https://quote-api.jup.ag/v4/quote?inputMint={token_in}&outputMint={token_out}&amount={amount}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json().get("data", [])[0]
    return None

# Execute swap transaction
def execute_swap(quote):
    swap_tx = requests.post("https://quote-api.jup.ag/v4/swap", json={
        "quoteResponse": quote,
        "userPublicKey": WALLET_ADDRESS,
        "wrapAndUnwrapSol": True
    })
    if swap_tx.status_code == 200:
        transaction = Transaction.deserialize(base58.b58decode(swap_tx.json().get("swapTransaction")))
        solana_client.send_transaction(transaction, opts=TxOpts(skip_preflight=True))
        return True
    return False

# Monitor price via WebSocket
async def monitor_price(pair_address):
    async with websockets.connect(f"wss://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}") as ws:
        while True:
            data = await ws.recv()
            price = float(data.get("priceUsd", 0))
            if price >= PROFIT_TARGET or price <= STOP_LOSS:
                # Execute sell order
                sell_quote = get_swap_quote(pair_address, "SOL", 1)  # Sell 1 token
                if sell_quote:
                    execute_swap(sell_quote)
                    break

# Main bot loop
async def main():
    while True:
        pairs = fetch_pairs()
        for pair in pairs:
            if apply_filters(pair):
                print(f"New pair found: {pair['baseToken']['symbol']}")
                buy_quote = get_swap_quote("SOL", pair["baseToken"]["address"], 1)  # Buy 1 SOL worth
                if buy_quote:
                    execute_swap(buy_quote)
                    asyncio.create_task(monitor_price(pair["pairAddress"]))
        await asyncio.sleep(60)  # Poll every minute

# Run the bot
if __name__ == "__main__":
    asyncio.run(main())