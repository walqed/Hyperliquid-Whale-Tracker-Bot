# 🐋 Hyperliquid Whale Tracker Bot

> Advanced Telegram bot for monitoring large trades and managing positions on Hyperliquid DEX

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Telegram](https://img.shields.io/badge/Platform-Telegram-blue.svg)](https://telegram.org/)
[![Hyperliquid](https://img.shields.io/badge/DEX-Hyperliquid-orange.svg)](https://hyperliquid.xyz/)

## Features

### 📊 Monitoring & Analytics
- **Real-time whale tracking**: Monitor large trades across Hyperliquid markets
- **Wallet activity analysis**: Track specific wallet transactions
- **Position monitoring**: View open positions, PnL, and leverage
- **Leaderboard integration**: Access top traders by period
- **Smart notifications**: Customizable thresholds for alerts

### 🎯 Trading Interface  
- **Market orders**: Quick buy/sell execution
- **Limit orders**: Precise order placement
- **Position management**: Close positions and adjust leverage
- **Order cancellation**: Cancel orders by ID

### 🔒 Security
- **Encrypted storage**: Private keys secured with Fernet encryption
- **Private trading**: Trading commands only in direct messages
- **Rate limiting**: Built-in API protection

## Quick Start

### Prerequisites
- Python 3.11+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Installation

```bash
git clone https://github.com/walqed/Hyperliquid-Whale-Tracker-Bot.git
cd Hyperliquid-Whale-Tracker-Bot
pip install -r requirements.txt
```

### Configuration
```bash
cp .env.example .env
# Edit .env and add your TELEGRAM_BOT_TOKEN
```

### Run
```bash
python main.py
```

## Commands

### Setup & Monitoring
```
/start                    - Welcome message
/add <address>           - Add wallet to tracking
/remove <address>        - Remove wallet
/list                    - Show tracked wallets
/set_threshold <amount>  - Set notification threshold
/positions <address>     - Show positions
/balance <address>       - Display balance
```

### Trading (Private Chat Only)
```
/set_key <private_key>   - Set trading key (secure)
/buy <coin> <usd>        - Open long position
/sell <coin> <usd>       - Open short position
/close <coin>            - Close position
/leverage <coin> <value> - Set leverage
```

### Advanced Orders
```
/order limit buy <coin> <size> @ <price>   - Limit buy order
/order limit sell <coin> <size> @ <price>  - Limit sell order
/cancel <coin> <order_id>                  - Cancel order
```

## Example Usage

```bash
# Monitor whale wallet
/add 0x742d35Cc6634C0532925a3b8D97a33b4E3c7F4e5

# Set $500K threshold
/set_threshold 500000

# Trading
/buy ETH 1000           # Buy $1000 worth of ETH
/leverage ETH 20        # Set 20x leverage
/close ETH              # Close position
```

## Security Notes

⚠️ **Important**: 
- Use `/set_key` only in private chat with bot
- Test with small amounts first
- Private keys are encrypted and stored locally
- Never share your private keys

## Architecture

- **main.py**: Bot entry point
- **handlers.py**: Telegram command handlers
- **hyperliquid_api.py**: Hyperliquid SDK integration  
- **trading.py**: Trading execution engine
- **monitoring.py**: WebSocket monitoring system
- **database.py**: Encrypted SQLite storage

## Contributing

1. Fork the repository
2. Create feature branch
3. Make changes
4. Submit pull request

## License

MIT License - see LICENSE file

## Disclaimer

This software is for educational purposes. Trading involves risk. Never trade more than you can afford to lose.
