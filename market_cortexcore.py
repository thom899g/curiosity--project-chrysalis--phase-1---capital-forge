"""
MARKET CORTEX: Central Nervous System for Project CHRYSALIS
Manages all market data, portfolio state, and execution logic with obsessive capital protection.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
from web3 import Web3
from web3.exceptions import TransactionNotFound
import firebase_admin
from firebase_admin import credentials, firestore
from pydantic import BaseModel, validator
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MARKET_CORTEX - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class MarketSnapshot:
    """Unified market data snapshot"""
    timestamp: datetime
    symbol: str
    exchange: str
    ohlcv: Dict[str, float]  # open, high, low, close, volume
    order_book: Dict[str, List[float]]
    funding_rate: Optional[float] = None
    gas_price: Optional[float] = None  # in gwei
    mempool_count: Optional[int] = None
    market_regime: str = "unknown"  # bull, bear, sideways, volatile
    
    @classmethod
    def from_ccxt(cls, ticker: Dict, symbol: str, exchange: str) -> 'MarketSnapshot':
        """Create snapshot from CCXT ticker data"""
        return cls(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            exchange=exchange,
            ohlcv={
                'open': ticker.get('open', 0),
                'high': ticker.get('high', 0),
                'low': ticker.get('low', 0),
                'close': ticker.get('close', 0),
                'volume': ticker.get('baseVolume', 0)
            },
            order_book={'bids': [], 'asks': []},  # Will be populated separately
            market_regime="unknown"
        )

class PortfolioState(BaseModel):
    """Portfolio state model with validation"""
    total_capital: float
    available_capital: float
    allocated_capital: float = 0.0
    positions: Dict[str, Dict[str, float]] = {}  # symbol -> {amount, entry_price, current_value}
    exposure_limit: float = 0.3  # Max 30% exposure
    daily_loss_limit: float = 0.05  # Max 5% daily loss
    max_drawdown: float = 0.15  # Max 15% drawdown
    
    @validator('exposure_limit')
    def validate_exposure_limit(cls, v):
        if v > 0.5:
            raise ValueError('Exposure limit cannot exceed 50%')
        return v

class MarketCortex:
    """Central nervous system for trading ecosystem"""
    
    def __init__(self, firebase_cred_path: str = "firebase_service_account.json"):
        # Initialize Firebase
        try:
            cred = credentials.Certificate(firebase_cred_path)
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            logger.info("Firebase initialized successfully")
        except Exception as e:
            logger.error(f"Firebase initialization failed: {e}")
            raise
        
        # Initialize exchanges
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self._init_exchanges()
        
        # Initialize Web3 for blockchain data
        self.web3_providers: Dict[str, Web3] = {}
        self._init_web3_providers()
        
        # State management
        self.portfolio_state = PortfolioState(
            total_capital=100.0,
            available_capital=100.0
        )
        self.market_data_cache: Dict[str, MarketSnapshot] = {}
        self.last_update = datetime.utcnow()
        
        # Risk limits
        self.global_risk_limits = {
            'max_position_size': 10.0,  # $10 max per position
            'max_daily_trades': 20,
            'cooldown_after_loss': timedelta(minutes=30)
        }
        
        # Performance tracking
        self.performance_metrics = {
            'total_trades': 0,
            'winning_trades': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'sharpe_ratio': 0.0
        }
    
    def _init_exchanges(self):
        """Initialize exchange connections with error handling"""
        exchange_configs = {
            'binance': {
                'apiKey': '',  # Will be loaded from environment
                'secret': '',
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            },
            'kraken': {
                'apiKey': '',
                'secret