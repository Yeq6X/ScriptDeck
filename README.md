# ScriptDeck

シンプルなスクリプトランチャー（PyQt6）。スクリプト一覧から選択して、右ペインで実行設定（venv・作業ディレクトリ・引数）をして実行できます。AI相談ペインから選択中スクリプトと質問をOpenAIに投げることも可能です。

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
```

環境変数 `OPENAI_API_KEY` を直接設定してもOKです。

## 直接起動（開発者向け）

```bash
python -m venv .venv
. .venv/bin/activate  # Windowsは .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
python main.py
```

## パッケージング（任意）

配布用の実行ファイルを作るには PyInstaller 等の利用を検討してください。

