# Production Readiness Checklist

## Prerequisites
- [ ] Exchange account with API keys (Binance, Bybit, or OKX)
- [ ] 2FA enabled on exchange
- [ ] API keys with trading permissions ONLY (no withdrawals)
- [ ] IP whitelist for API keys
- [ ] $500 USDT deposited

## Configuration
- [ ] Set exchange API keys in `.env`
- [ ] Configure risk parameters for real trading
- [ ] Set up proper logging
- [ ] Configure alerts (email/Telegram)

## Risk Management
- [ ] Max drawdown: 5% (down from 10%)
- [ ] Max position size: 10% (down from 25%)
- [ ] Max concurrent positions: 3 (down from 10)
- [ ] Stop-loss: 2% (already set)
- [ ] Take-profit: 4% (2:1 R:R)
- [ ] Cooldown between trades: 5 minutes

## Testing
- [ ] Paper trade for 1 week with new risk params
- [ ] Verify stop-loss triggers work
- [ ] Test order execution speed
- [ ] Monitor slippage

## Deployment
- [ ] Start with $100 for first 3 days
- [ ] Scale to $200 if profitable
- [ ] Scale to $500 after 1 week
- [ ] Never exceed $500 without manual approval

## Monitoring
- [ ] Check dashboard daily
- [ ] Review circuit breaker trips
- [ ] Monitor GPU/LLM performance
- [ ] Track win rate by strategy
