import os
import time
import requests
import asyncio
import websockets
import pandas as pd
import psycopg2
import base58
import logging
from solana.rpc.api import Client
from solders.transaction import Transaction
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction, TransactionError
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info("Environment variables loaded")

# Configuration
DEX_SCREENER_API = os.getenv("DEX_SCREENER_API_KEY")
JUPITER_API = os.getenv("JUPITER_API_KEY")
SOLANA_RPC = os.getenv("SOLANA_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

def clean_env_value(value):
    if value is None:
        logger.error("Missing environment variable")
        return None
    return value.split('#')[0].strip()

# Load and validate trading parameters
try:
    MIN_LIQUIDITY = float(clean_env_value(os.getenv("MIN_LIQUIDITY")))
    MIN_VOLUME = float(clean_env_value(os.getenv("MIN_VOLUME")))
    MAX_CONTRACT_AGE = int(clean_env_value(os.getenv("MAX_CONTRACT_AGE")))
    PROFIT_TARGET = float(clean_env_value(os.getenv("PROFIT_TARGET")))
    STOP_LOSS = float(clean_env_value(os.getenv("STOP_LOSS")))
    logger.info("Trading parameters loaded successfully")
except (ValueError, TypeError) as e:
    logger.error(f"Error loading trading parameters: {e}")
    exit(1)

# Database connection
try:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )
    cursor = conn.cursor()
    logger.info("Database connection established successfully")
except psycopg2.Error as e:
    logger.error(f"Database connection error: {e}")
    exit(1)

# Solana client
try:
    solana_client = Client(SOLANA_RPC)
    logger.info("Solana client initialized")
except Exception as e:
    logger.error(f"Error initializing Solana client: {e}")
    exit(1)

# Fetch new pairs from DEX Screener
def fetch_pairs():
    try:
        # Updated to the correct endpoint with chain parameter
        url = "https://api.dexscreener.com/latest/dex/search?q=solana"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 429:  # Rate limit hit
            logger.warning("Rate limit reached, waiting before retry...")
            time.sleep(60)  # Wait for 60 seconds before retry
            return []
            
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "pairs" in data:
                pairs = data["pairs"]
                # Filter only Solana pairs
                solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                logger.info(f"Successfully fetched {len(solana_pairs)} Solana pairs from DEX Screener")
                return solana_pairs
            else:
                logger.warning("Unexpected response structure from DEX Screener")
                logger.debug(f"Response: {data}")
                return []
                
        logger.warning(f"DEX Screener API returned status code: {response.status_code}")
        logger.warning(f"Response content: {response.text[:200]}...")  # Log first 200 chars of response
        return []
    except Exception as e:
        logger.error(f"Error fetching pairs: {e}")
        logger.debug(f"Full error: {str(e)}")
        return []

# Apply trading filters
def apply_filters(pair):
    try:
        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
        volume = float(pair.get("volume", {}).get("h24", 0))
        created_at = pair.get("pairCreatedAt", 0)
        age = time.time() - (created_at / 1000)  # Convert to seconds
        
        meets_criteria = (
            liquidity >= MIN_LIQUIDITY and 
            volume >= MIN_VOLUME and 
            age <= MAX_CONTRACT_AGE
        )
        
        if meets_criteria:
            logger.info(f"Pair meets criteria - Liquidity: ${liquidity:,.2f}, Volume: ${volume:,.2f}, Age: {age/3600:.2f}h")
            logger.info(f"Pair details: {pair.get('baseToken', {}).get('symbol')} / {pair.get('quoteToken', {}).get('symbol')}")
            logger.info(f"DEX: {pair.get('dexId')} - Price: ${float(pair.get('priceUsd', 0)):,.8f}")
        
        return meets_criteria
    except Exception as e:
        logger.error(f"Error applying filters: {e}")
        return False

# Get swap quote from Jupiter API
def get_swap_quote(token_in, token_out, amount):
    try:
        url = f"https://quote-api.jup.ag/v4/quote?inputMint={token_in}&outputMint={token_out}&amount={amount}"
        response = requests.get(url)
        if response.status_code == 200:
            quote = response.json().get("data", [])[0]
            logger.info(f"Successfully got swap quote for {token_in} -> {token_out}")
            return quote
        logger.warning(f"Jupiter API returned status code: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Error getting swap quote: {e}")
        return None

# Execute swap transaction
def execute_swap(quote):
    try:
        swap_tx = requests.post("https://quote-api.jup.ag/v4/swap", json={
            "quoteResponse": quote,
            "userPublicKey": WALLET_ADDRESS,
            "wrapAndUnwrapSol": True
        })
        if swap_tx.status_code == 200:
            try:
                transaction = Transaction.from_bytes(base58.b58decode(swap_tx.json().get("swapTransaction")))
                solana_client.send_transaction(transaction, skip_preflight=True)
                logger.info("Swap transaction executed successfully")
                return True
            except TransactionError as e:
                logger.error(f"Transaction error: {e}")
                return False
            except Exception as e:
                logger.error(f"Error executing swap: {e}")
                return False
        logger.warning(f"Swap API returned status code: {swap_tx.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error in execute_swap: {e}")
        return False

# Monitor price via WebSocket
async def monitor_price(pair_address):
    try:
        logger.info(f"Starting price monitoring for pair: {pair_address}")
        async with websockets.connect(f"wss://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}") as ws:
            while True:
                data = await ws.recv()
                price = float(data.get("priceUsd", 0))
                logger.info(f"Current price for {pair_address}: ${price:,.8f}")
                if price >= PROFIT_TARGET or price <= STOP_LOSS:
                    logger.info(f"Price target reached. Executing sell order at ${price:,.8f}")
                    sell_quote = get_swap_quote(pair_address, "SOL", 1)  # Sell 1 token
                    if sell_quote:
                        execute_swap(sell_quote)
                        break
    except Exception as e:
        logger.error(f"Error in price monitoring: {e}")

# Main bot loop
async def main():
    logger.info("Starting main bot loop")
    while True:
        try:
            pairs = fetch_pairs()
            if pairs:  # Only process if we actually got pairs
                logger.info(f"Processing {len(pairs)} pairs")
                for pair in pairs:
                    if apply_filters(pair):
                        symbol = pair['baseToken']['symbol']
                        logger.info(f"New pair found: {symbol}")
                        buy_quote = get_swap_quote("SOL", pair["baseToken"]["address"], 1)  # Buy 1 SOL worth
                        if buy_quote:
                            if execute_swap(buy_quote):
                                logger.info(f"Successfully bought {symbol}")
                                asyncio.create_task(monitor_price(pair["pairAddress"]))
            else:
                logger.warning("No pairs fetched in this iteration")
            
            logger.info("Waiting for next iteration...")
            await asyncio.sleep(60)  # Poll every minute
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(60)  # Wait before retrying

# Run the bot
if __name__ == "__main__":
    logger.info("=== Starting Meme Coin Trading Bot ===")
    logger.info("Configuration:")
    logger.info(f"MIN_LIQUIDITY: ${MIN_LIQUIDITY:,.2f}")
    logger.info(f"MIN_VOLUME: ${MIN_VOLUME:,.2f}")
    logger.info(f"MAX_CONTRACT_AGE: {MAX_CONTRACT_AGE} seconds")
    logger.info(f"PROFIT_TARGET: {PROFIT_TARGET}x")
    logger.info(f"STOP_LOSS: {STOP_LOSS}x")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")