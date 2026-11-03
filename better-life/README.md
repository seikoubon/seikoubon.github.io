# Better lAIfe

米国発の記事・書籍公式紹介を日本語で紹介する読書室。

公開: https://seikoubon.github.io/better-life/

## 構成
- index.html: レスポンシブUI、検索、カテゴリー、記事ダイアログ、ブラウザー内お気に入り
- articles.json: 全記事。配列を保持して追加する。HTMLは再生成不要。

## 運用
日本時間7時・12時・17時にChatGPTの定期タスク「米国発 Better lAIfe 投稿」が一次情報を確認し、GitHub APIでarticles.jsonを更新する。GitHub Actionsや外部AI APIキーは不要。タスクの接続状態・実行制限やGitHub Pagesの反映待ちにより更新が遅れることがある。初回の自動更新は実行後に確認する。

## 編集方針
米国著者・出版社・メディア発限定。原文を実読、公式プロフィール等で米国発を確認。米国Amazon Business & Money、NYT Business Books、Barnes & Noble等のランキングを選定の参考にする。アクセス不能なランキングの順位を補完しない。確認できた順位には部門、日時、出典URLを記す。新刊・新着と過去記事を区別。全文翻訳や転載でなく独自紹介と実践提案を分ける。

## データ
{id,date,slot,category,title,author,work,source,checked,summary,sections:[[heading,body]]}。idは日本時間YYYYMMDD-HHMMで一意。categoryは書籍、仕事術・ブログ、ミニマリズム、ライフハック。sourceはhttps URL。表示テキストはHTMLエスケープする。

## 初期記事
2026-11-03に原文を確認した10件のサンプル。書籍の全文読了に基づく書評ではなく、公開原文に基づく案内。ランキングの現在順位は確認できていないため掲載していない。

## 本文の分量
1本約2,000文字（目安1,800〜2,200文字）。sections各項目の本文だけを数え、タイトル・見出し・出典URLを除く。10本の初期記事も同基準へ拡充済み。原文の要約を必要な範囲に留め、追加の分析・仮想例・実践案には編集部独自であることを明記し、水増しの反復や出典にない事実の追加を避ける。定期投稿タスクも同じ基準に設定。
