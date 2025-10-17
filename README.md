# ScriptDeck

シンプルなスクリプトランチャー（PyQt6）。スクリプト一覧から選択して、右ペインで実行設定（venv・作業ディレクトリ・引数）をして実行できます。AI相談ペインから選択中スクリプトと質問をOpenAIに投げることも可能です。
<img width="1711" height="1047" alt="image" src="https://github.com/user-attachments/assets/853d8713-97de-4682-b243-07bccd262fce" />

## 起動

最も手軽な方法は、同梱の起動スクリプトを使う方法です。初回は自動で `.venv` を作成し、依存をインストールします。

- Windows（PowerShell）: `Start.ps1` を右クリックして「PowerShellで実行」
- Windows（コマンドプロンプト）: `start.bat` をダブルクリック
- macOS/Linux: ターミナルで `./start.sh`

※ ポリシーによりPowerShellのスクリプト実行がブロックされる場合は、`start.bat` を使うか、一時的に `Set-ExecutionPolicy -Scope Process Bypass` を実行してから `Start.ps1` を起動してください。

## 必要条件

- Python 3.10 以上（推奨）
- ネットワーク接続（AI機能を使う場合）

## OpenAI の設定（任意）

AIペインから相談するには API キーが必要です。

1. ルートに `.env` を作成
2. 以下を記述

```
OPENAI_API_KEY=sk-...（あなたのキー）
# 任意: 使用するモデル（未指定時は gpt-5 を使用）
OPENAI_MODEL=gpt-4o
```

- 環境変数 `OPENAI_API_KEY` / `OPENAI_MODEL` を直接設定してもOKです。
- `OPENAI_MODEL` を省略した場合は既定の `gpt-5` が使われます。

## 直接起動

```bash
python -m venv .venv
. .venv/bin/activate  # Windowsは .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
python main.py
```

## リアルタイムログ（長時間実行/Gradioなど）

ScriptDeck は実行時に Python をアンバッファ（`-u` と `PYTHONUNBUFFERED=1`）で起動するため、標準出力/標準エラーがリアルタイムに右ペインへ表示されます。もし依然として出力が遅延する場合は、スクリプト側で `print(..., flush=True)` を使うか、`sys.stdout.reconfigure(line_buffering=True)` を検討してください。

## パッケージング（任意）

配布用の実行ファイルを作るには PyInstaller 等の利用を検討してください。
