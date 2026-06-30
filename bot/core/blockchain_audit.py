"""
Auditoría de operaciones - Versión local.
Guarda pruebas inmutables en JSON con hash SHA-256.
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict
from loguru import logger

AUDIT_DIR = Path("data/blockchain_proofs")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TradeProof:
    trade_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    pnl_usdt: float
    pnl_pct: float
    entry_time: str
    exit_time: str
    hash_signature: str
    bot_version: str
    created_at: str


class BlockchainAudit:
    def __init__(self) -> None:
        self.index_file = AUDIT_DIR / "index.json"
        self._load_index()

    def _load_index(self) -> None:
        if self.index_file.exists():
            with open(self.index_file) as f:
                self.index = json.load(f)
        else:
            self.index = {"trades": [], "last_id": 0}

    def _save_index(self) -> None:
        with open(self.index_file, "w") as f:
            json.dump(self.index, f, indent=2)

    def _generate_hash(self, trade_data: dict[str, Any]) -> str:
        fields = [
            trade_data.get("trade_id", ""),
            trade_data.get("symbol", ""),
            trade_data.get("side", ""),
            str(trade_data.get("entry_price", 0)),
            str(trade_data.get("exit_price", 0)),
            str(trade_data.get("pnl_usdt", 0)),
            trade_data.get("entry_time", ""),
        ]
        return hashlib.sha256("|".join(fields).encode()).hexdigest()

    def log_trade(self, trade_data: dict[str, Any]) -> str:
        self.index["last_id"] += 1
        trade_id = trade_data.get("trade_id", f"trade_{self.index['last_id']:06d}")
        proof = TradeProof(
            trade_id=trade_id,
            symbol=trade_data.get("symbol", ""),
            side=trade_data.get("side", ""),
            entry_price=float(trade_data.get("entry_price", 0)),
            exit_price=float(trade_data.get("exit_price", 0)),
            pnl_usdt=float(trade_data.get("pnl_usdt", 0)),
            pnl_pct=float(trade_data.get("pnl_pct", 0)),
            entry_time=trade_data.get("entry_time", ""),
            exit_time=trade_data.get("exit_time", ""),
            hash_signature=self._generate_hash(trade_data),
            bot_version="4.0",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        proof_file = AUDIT_DIR / f"{trade_id}.json"
        with open(proof_file, "w") as f:
            json.dump(asdict(proof), f, indent=2)
        self.index["trades"].append({"trade_id": trade_id, "hash": proof.hash_signature[:16]})
        self._save_index()
        ledger = AUDIT_DIR / "ledger.txt"
        with open(ledger, "a") as f:
            f.write(f"{proof.exit_time}|{trade_id}|{proof.hash_signature}\n")
        logger.info(f"🔗 Trade auditado: {trade_id}")
        return str(trade_id)

    def verify_trade(self, trade_id: str) -> dict[str, Any]:
        proof_file = AUDIT_DIR / f"{trade_id}.json"
        if not proof_file.exists():
            return {"valid": False, "error": "Trade no encontrado"}
        with open(proof_file) as f:
            stored = json.load(f)
        current_hash = self._generate_hash(stored)
        return {
            "valid": current_hash == stored["hash_signature"],
            "trade_id": trade_id,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_stats(self) -> dict[str, Any]:
        proofs = list(AUDIT_DIR.glob("*.json"))
        return {
            "total_trades_audited": len([p for p in proofs if p.name != "index.json"]),
            "mode": "local",
            "proofs_directory": str(AUDIT_DIR),
            "immutable_ledger": (AUDIT_DIR / "ledger.txt").exists(),
        }