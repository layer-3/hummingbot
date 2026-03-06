![Hummingbot](https://github.com/user-attachments/assets/3213d7f8-414b-4df8-8c1b-a0cd142a82d8)

----

[![License](https://img.shields.io/badge/License-Apache%202.0-informational.svg)](https://github.com/hummingbot/hummingbot/blob/master/LICENSE)
[![Based on Hummingbot](https://img.shields.io/badge/based%20on-hummingbot%2Fhummingbot-blue)](https://github.com/hummingbot/hummingbot)
[![Yellow Pro Connector](https://img.shields.io/badge/connector-yellow__pro-yellow)](https://yellow.pro)

<div align="center">

### This is a fork of [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) with the addition of the Yellow Pro (`yellow_pro`) spot exchange connector.

**The `master` branch tracks upstream Hummingbot. All Yellow Pro additions live on the [`yellow-pro`](../../tree/yellow-pro) branch.**

</div>

---

## Yellow Pro Connector

[Yellow Pro](https://yellow.pro) is a centralized spot exchange. This fork adds a native Hummingbot connector for it.

### Connector Details

| Property | Value |
|---|---|
| Connector ID | `yellow_pro` |
| Type | CLOB CEX — Spot |
| Authentication | API Key + Secret |
| Branch | [`yellow-pro`](../../tree/yellow-pro) |
| Connector path | `hummingbot/connector/exchange/yellow_pro/` |

### Files Added

```
hummingbot/connector/exchange/yellow_pro/
├── yellow_pro_exchange.py                   # Main exchange class
├── yellow_pro_api_order_book_data_source.py # Order book via REST/WebSocket
├── yellow_pro_api_user_stream_data_source.py# User stream via WebSocket
├── yellow_pro_auth.py                       # API key authentication
├── yellow_pro_constants.py                  # URLs, rate limits, order states
├── yellow_pro_order_book.py                 # Order book snapshot/diff handling
├── yellow_pro_utils.py                      # Utility helpers
├── yellow_pro_web_utils.py                  # HTTP/WS factory helpers
├── yellow_pro_perf_tracer.py                # Performance tracing
└── docs/
    ├── API.md                               # REST API reference
    └── WS.md                               # WebSocket API reference

scripts/
├── yellow_pro_market_making.py              # Market making example script
├── yellow_pro_market_data_dumper.py         # Market data logging script
└── yellow_pro_order_test.py                 # Order placement test script
```

### Usage

1. Clone this repo and switch to the `yellow-pro` branch:

```bash
git clone https://github.com/layer-3/hummingbot.git
cd hummingbot
git checkout yellow-pro
```

2. Install and run Hummingbot (see [Getting Started](#getting-started) below).

3. Connect the Yellow Pro connector:

```
connect yellow_pro
```

4. Enter your API key and secret when prompted.

### Syncing with Upstream Hummingbot

```bash
# Update master to match upstream
git checkout master
git fetch upstream
git merge upstream/master
git push origin master

# Bring yellow-pro up to date
git checkout yellow-pro
git merge master
git push origin yellow-pro
```

---

## About Hummingbot

Hummingbot is an open-source framework that helps you design and deploy automated trading strategies, or **bots**, that can run on many centralized or decentralized exchanges. Over the past year, Hummingbot users have generated over $34 billion in trading volume across 140+ unique trading venues.

The Hummingbot codebase is free and publicly available under the Apache 2.0 open-source license. Our mission is to **democratize high-frequency trading** by creating a global community of algorithmic traders and developers that share knowledge and contribute to the codebase.

### Quick Links

* [Website and Docs](https://hummingbot.org): Official Hummingbot website and documentation
* [Installation](https://hummingbot.org/installation/docker/): Install Hummingbot on various platforms
* [Discord](https://discord.gg/hummingbot): The main gathering spot for the global Hummingbot community
* [YouTube](https://www.youtube.com/c/hummingbot): Videos that teach you how to get the most out of Hummingbot
* [Twitter](https://twitter.com/_hummingbot): Get the latest announcements about Hummingbot

## Getting Started

The easiest way to get started with Hummingbot is using Docker:

* To install the Telegram Bot [Condor](https://github.com/hummingbot/condor), follow the instructions in the [Hummingbot Docs](https://hummingbot.org/condor/installation/) site.

* To install the CLI-based Hummingbot client, follow the instructions below.

Alternatively, if you are building new connectors/strategies or adding custom code, see the [Install from Source](https://hummingbot.org/client/installation/#source-installation) section in the documentation.

### Install Hummingbot with Docker

Install [Docker Compose website](https://docs.docker.com/compose/install/).

Clone this repo, switch to the `yellow-pro` branch, and use the provided `docker-compose.yml` file:

```bash
git clone https://github.com/layer-3/hummingbot.git
cd hummingbot
git checkout yellow-pro

make setup
make deploy

docker attach hummingbot
```

### Install Hummingbot + Gateway DEX Middleware

```bash
git clone https://github.com/layer-3/hummingbot.git
cd hummingbot
git checkout yellow-pro
make setup
# Answer `y` when prompted: Include Gateway? [y/N]
make deploy
docker attach hummingbot
```

---

For comprehensive installation instructions and troubleshooting, visit the [Hummingbot Installation Docs](https://hummingbot.org/installation/).

## Getting Help

* Consult the [Hummingbot FAQ](https://hummingbot.org/faq/), [Troubleshooting Guide](https://hummingbot.org/troubleshooting/), or [Glossary](https://hummingbot.org/glossary/)
* For Yellow Pro connector issues, open an issue in this repository
* For general Hummingbot issues, submit a [Github issue](https://github.com/hummingbot/hummingbot/issues) upstream
* Join the [Hummingbot Discord community](https://discord.gg/hummingbot)

## License

This repository is licensed under the [Apache 2.0 License](LICENSE), the same as the upstream Hummingbot project.

The Yellow Pro connector (`hummingbot/connector/exchange/yellow_pro/`) is original work added by [layer-3](https://github.com/layer-3), also released under Apache 2.0.

Copyright notices from the original Hummingbot project are preserved in full.
