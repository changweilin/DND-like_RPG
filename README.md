# DND-like RPG 文本角色扮演遊戲引擎

這是一個基於回合制、由本地 AI 模型驅動的文字 RPG 遊戲引擎。玩家可以建立角色，並與「AI 遊戲大師 (Game Master)」進行互動。AI GM 會在每個回合生成敘事、提供選項，並根據遊戲規則處理機制結果。遊戲還可透過本地的擴散模型（Diffusion Model）選擇性地生成場景圖片。

## 專案特色 (Features)
- **純本地端執行**：不依賴外部 API，保護隱私且無使用配額限制。
- **神經符號系統 (Neuro-Symbolic Design)**：將 LLM 作為單純的「意圖解析器」和「敘事生成器」，而核心的血量、傷害、擲骰子檢定則保留由傳統的 Python 程式碼確定性地處理，避免 AI 對遊戲機制的幻覺（Hallucination）。
- **RAG 動態記憶機制**：使用 ChromaDB 向量資料庫儲存並檢索「世界觀設定 (World Lore)」、「故事事件 (Story Events)」及「遊戲規則 (Game Rules)」，確保劇情與知識的上下文連貫。
- **本地圖像生成**：整合 Diffusers (SDXL-Turbo)，根據當前場景動態即時生成視覺畫面。
- **NPC 獨立社交代理**：遊戲內 NPC 具有獨立情感狀態，會根據互動狀況獨立更新目標與對玩家的好感度。

## 技術棧 (Tech Stack)
- **前端 UI**：Streamlit
- **本地 LLM 服務**：Ollama + Llama3 (或其他本機模型如 Qwen2.5, Breeze-7B)
- **向量資料庫 (RAG)**：ChromaDB
- **關聯式資料庫 (存檔系統)**：SQLite + SQLAlchemy
- **圖像生成**：PyTorch + Diffusers (SDXL-Turbo)

## 安裝與執行 (Getting Started)

1. **安裝依賴** (請確保你的環境中已安裝對應的 Python 套件與 Ollama)。
2. **啟動 Ollama 模型**：
   ```bash
   ollama pull llama3
   ollama serve
   ```
3. **啟動遊戲伺服器**：
   在專案根目錄執行以下指令：
   ```bash
   python run.py
   ```
   此腳本會自動設定環境變數並啟動 Streamlit 前端介面（實際上會呼叫 `streamlit run ui/app.py`）。

## 系統硬體建議 (Hardware Recommendations)
所有的系統參數皆可至 `engine/config.py` 中調整，建議依據你的顯示卡 VRAM 進行設定：
- **RTX 3060 (12 GB VRAM)**：建議使用 `jcai/breeze-7b-instruct-v1_0-gguf` 或預設的 `llama3`。為了讓 12GB 的顯存能同時應付 LLM 和圖像生成的 SDXL-Turbo 模型，系統支援動態載入與卸載模型的策略（Strategy B）。
- **RTX 4090 (24 GB VRAM)**：建議使用 `qwen2.5:32b` 或其他更高階的模型以獲得極佳的雙語推理能力與文學敘事體驗。

## 專案架構 (Repository Layout)
- `/ai/`：包含 LLM 用戶端介接、RAG 向量知識庫系統與圖像生成。
- `/engine/`：核心遊戲規則引擎，包含實體角色狀態、權威的擲骰系統 (DiceRoller)、SQLite 存檔載入與世界狀態管理。
- `/logic/`：事件協調器（EventManager），嚴謹處理包含玩家意圖解析、判定、計算、敘事生成與記憶儲存等 10 個核心步驟的完整回合邏輯。
- `/ui/`：Streamlit 前端介面程式碼。
- `/tools/`：輔助工具集，例如將 D&D 5e SRD 規則匯入知識庫的工具，以及產生 LoRA 微調模型訓練資料的工具。

---

> **給開發者 / AI 助理的提示：** 如需詳細的架構解說、資料庫綱要與修改規範，請務必參閱本專案根目錄中的 **`CLAUDE.md`** 文件。
