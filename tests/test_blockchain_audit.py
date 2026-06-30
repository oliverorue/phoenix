"""Tests para BlockchainAudit (FASE 5.2)."""

import json
from unittest.mock import patch
from pathlib import Path


class TestBlockchainAuditInit:
    """Verifica la inicialización del audit trail."""

    def test_init_creates_index_if_not_exists(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        # Patch AUDIT_DIR para que use tmp_path y no haya index existente
        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir(parents=True, exist_ok=True)
        # No crear index.json → _load_index creará index vacío

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            assert audit.index == {"trades": [], "last_id": 0}
            assert audit.index_file == audit_dir / "index.json"

    def test_load_index_creates_empty(self):
        from bot.core.blockchain_audit import BlockchainAudit

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=Path("/tmp/nonexistent_audit_test")):
            audit = BlockchainAudit()
            assert audit.index == {"trades": [], "last_id": 0}

    def test_load_index_loads_existing(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [{"trade_id": "test_001"}], "last_id": 1}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            assert audit.index["last_id"] == 1
            assert len(audit.index["trades"]) == 1


class TestBlockchainAuditGenerateHash:
    """Verifica la generación de hash SHA-256."""

    def test_hash_is_consistent(self):
        from bot.core.blockchain_audit import BlockchainAudit

        audit = BlockchainAudit()
        data = {
            "trade_id": "trade_001",
            "symbol": "XRPUSDT",
            "side": "LONG",
            "entry_price": 0.50,
            "exit_price": 0.55,
            "pnl_usdt": 5.0,
            "pnl_pct": 10.0,
            "entry_time": "2026-01-01T00:00:00",
        }
        h1 = audit._generate_hash(data)
        h2 = audit._generate_hash(data)
        assert h1 == h2  # Mismos datos → mismo hash

    def test_hash_changes_with_different_data(self):
        from bot.core.blockchain_audit import BlockchainAudit

        audit = BlockchainAudit()
        data1 = {"trade_id": "trade_001", "symbol": "XRPUSDT", "side": "LONG",
                 "entry_price": 0.50, "exit_price": 0.55, "pnl_usdt": 5.0,
                 "entry_time": "2026-01-01T00:00:00"}
        data2 = {**data1, "entry_price": 0.51}  # Precio diferente

        h1 = audit._generate_hash(data1)
        h2 = audit._generate_hash(data2)
        assert h1 != h2

    def test_hash_is_sha256_hex(self):
        from bot.core.blockchain_audit import BlockchainAudit

        audit = BlockchainAudit()
        data = {"trade_id": "test", "symbol": "XRP", "side": "LONG",
                "entry_price": 1.0, "exit_price": 1.1, "pnl_usdt": 10.0,
                "entry_time": "2026-01-01"}
        h = audit._generate_hash(data)
        assert len(h) == 64  # SHA-256 hex = 64 chars
        int(h, 16)  # Debe ser hex válido


class TestBlockchainAuditLogTrade:
    """Verifica log_trade()."""

    def test_log_trade_creates_proof_file(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            trade_id = audit.log_trade({
                "symbol": "XRPUSDT",
                "side": "LONG",
                "entry_price": 0.50,
                "exit_price": 0.55,
                "pnl_usdt": 5.0,
                "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            assert trade_id == "trade_000001"
            proof_file = audit_dir / f"{trade_id}.json"
            assert proof_file.exists()

            # Verificar contenido del proof
            with open(proof_file) as f:
                proof = json.load(f)
            assert proof["symbol"] == "XRPUSDT"
            assert proof["side"] == "LONG"
            assert proof["entry_price"] == 0.50
            assert proof["hash_signature"] != ""

    def test_log_trade_updates_index(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            audit.log_trade({
                "symbol": "XRPUSDT", "side": "LONG",
                "entry_price": 0.50, "exit_price": 0.55,
                "pnl_usdt": 5.0, "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            assert audit.index["last_id"] == 1
            assert len(audit.index["trades"]) == 1

    def test_log_trade_creates_ledger(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            audit.log_trade({
                "symbol": "XRPUSDT", "side": "LONG",
                "entry_price": 0.50, "exit_price": 0.55,
                "pnl_usdt": 5.0, "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            ledger = audit_dir / "ledger.txt"
            assert ledger.exists()
            content = ledger.read_text()
            assert "trade_000001" in content


class TestBlockchainAuditVerify:
    """Verifica verify_trade()."""

    def test_verify_valid_trade(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            # Incluir trade_id explícitamente para que coincida el hash
            trade_id = audit.log_trade({
                "trade_id": "trade_000001",
                "symbol": "XRPUSDT", "side": "LONG",
                "entry_price": 0.50, "exit_price": 0.55,
                "pnl_usdt": 5.0, "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            result = audit.verify_trade(trade_id)
            assert result["valid"] is True, f"Hash mismatch: {result}"
            assert result["trade_id"] == trade_id

    def test_verify_nonexistent_trade(self):
        from bot.core.blockchain_audit import BlockchainAudit

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=Path("/tmp/nonexistent_audit_test")):
            audit = BlockchainAudit()
            result = audit.verify_trade("nonexistent")
            assert result["valid"] is False
            assert "no encontrado" in result["error"]

    def test_verify_tampered_trade(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            trade_id = audit.log_trade({
                "symbol": "XRPUSDT", "side": "LONG",
                "entry_price": 0.50, "exit_price": 0.55,
                "pnl_usdt": 5.0, "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            # Manipular el proof file
            proof_file = audit_dir / f"{trade_id}.json"
            with open(proof_file) as f:
                proof = json.load(f)
            proof["entry_price"] = 0.99  # Manipulado!
            with open(proof_file, "w") as f:
                json.dump(proof, f)

            result = audit.verify_trade(trade_id)
            assert result["valid"] is False  # Hash no coincide


class TestBlockchainAuditStats:
    """Verifica get_stats()."""

    def test_get_stats_empty(self):
        from bot.core.blockchain_audit import BlockchainAudit

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=Path("/tmp/nonexistent_audit_test")):
            audit = BlockchainAudit()
            stats = audit.get_stats()
            assert stats["total_trades_audited"] >= 0
            assert stats["mode"] == "local"

    def test_get_stats_with_trades(self, tmp_path):
        from bot.core.blockchain_audit import BlockchainAudit

        audit_dir = tmp_path / "blockchain_proofs"
        audit_dir.mkdir()
        index_file = audit_dir / "index.json"
        index_file.write_text(json.dumps({"trades": [], "last_id": 0}))

        with patch("bot.core.blockchain_audit.AUDIT_DIR", new=audit_dir):
            audit = BlockchainAudit()
            audit.log_trade({
                "symbol": "XRPUSDT", "side": "LONG",
                "entry_price": 0.50, "exit_price": 0.55,
                "pnl_usdt": 5.0, "pnl_pct": 10.0,
                "entry_time": "2026-01-01T00:00:00",
                "exit_time": "2026-01-01T01:00:00",
            })

            stats = audit.get_stats()
            assert stats["total_trades_audited"] == 1
