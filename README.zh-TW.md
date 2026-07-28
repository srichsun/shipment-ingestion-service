[English](README.md) · **繁體中文**

# Shipment Ingestion Service（出貨事件接收服務）

一個小型的 FastAPI 服務。倉庫把貨出掉後會發出一筆「出貨事件」，這個服務負責接住它：先確認來源身分，再檢查資料對不對，套用一條商業規則，最後把乾淨的事件轉給下游做報表與分析。

## 📖 完整說明 + 互動 demo

### → https://srichsun.github.io/shipment-ingestion-service/

問題背景、請求流程、設計取捨、API 參考，還有一個可以直接在瀏覽器點的 demo，全都在那裡。下面只放「跑起來」需要的最少東西。

![系統流程](docs/architecture.svg)

## 快速開始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export API_KEY=dev-local-key          # 或寫進 .env
uvicorn app.main:app --reload         # 互動文件在 http://localhost:8000/docs
pytest                                # 跑測試
```

## 技術

Python 3.11+ · FastAPI · Pydantic v2 · pytest · GitHub Actions CI

> 個人 demo 專案，練習把一個電商出貨的整合服務設計得穩一點、也好改一點。
