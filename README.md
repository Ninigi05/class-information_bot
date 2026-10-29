# Class information bot（授業情報Bot）

大学の時間割を登録しておくと、授業の前に Discord の DM で通知してくれる Bot です。
Discord のスラッシュコマンドと Web ダッシュボードの両方から時間割・通知・補講・休講などを管理できます。

- **利用者の方** → [使い方](#使い方) と [コマンド一覧](#コマンド一覧)
- **動作の詳細を知りたい方** → [仕様](#仕様)
- **Bot を自分で動かす方** → [セットアップと運用](#セットアップと運用)

---

## 目次

1. [できること](#できること)
2. [使い方](#使い方)
3. [コマンド一覧](#コマンド一覧)
4. [仕様](#仕様)
5. [Web ダッシュボードと Web API](#web-ダッシュボードと-web-api)
6. [セットアップと運用](#セットアップと運用)
7. [ファイル構成](#ファイル構成)

---

## できること

| 機能 | 内容 |
| --- | --- |
| 時間割の登録 | 曜日・時限・科目名・教室を前期／後期ごとに登録 |
| 授業前通知 | 授業開始の N 分前に DM で通知（1 授業につき 2 回） |
| 朝の一覧通知 | 毎朝指定した時刻に、その日の授業一覧を DM で送信 |
| 曜日変更（振替） | 指定日だけ別の曜日の時間割で動かす |
| 教室変更 | 指定日・指定時限の教室だけ変更する |
| 補講 | 指定日・指定時限に、登録済みの授業を追加する |
| 休講 | 指定日の特定の授業を通知せず、授業回数にも数えない |
| 試験期間 | 期間中は通常の時間割の代わりに試験用時間割で通知 |
| 授業回数 | 学期の授業回数を設定すると、回数に達した授業は自動で削除され通知が止まる |
| 時限の時刻変更 | 各時限の開始時刻を自分の大学に合わせて変更 |
| Gmail 連携（任意） | Gmail から休講・授業変更のメールを探して DM に表示 |
| Web ダッシュボード | Discord でログインし、ブラウザから時間割や補講・休講を編集 |

---

## 使い方

### 1. はじめる

1. 案内された Discord サーバーに参加します。参加すると Bot から使い方の DM が届きます。
   - このサーバーは窓口専用です。DM を送ったあと自動的にサーバーから外れますが、Bot との DM はそのまま使えます。
2. Bot との DM（またはサーバーのチャンネル）で `/help` を実行すると、コマンド一覧が DM で届きます。
3. 以降の操作は Bot との DM で行えます。

> **DM が届かない場合**：Discord の「プライバシー設定」でサーバーメンバーからの DM を許可してください。
> この Bot のほとんどの結果は DM で届きます。

### 2. 学期の期間を設定する（推奨）

通知を送る期間を学期ごとに設定します。設定しない場合は月で判定します（4〜9 月が前期、それ以外が後期）。

```
/setting term_range term:前期 start_date:2026-04-08 end_date:2026-07-31
/setting term_range term:後期 start_date:2026-10-01 end_date:2027-01-31
```

期間を設定すると、**どちらの学期の期間にも入らない日（長期休暇など）は授業前通知・朝の一覧が送られません。**

### 3. 時間割を登録する

**Discord から**

```
/class add weekday:月曜日 period:1 subject:線形代数 room:A101
```

- 登録先は「現在の学期」です（`/class status` で確認できます）。
- 同じ曜日・時限に登録すると上書きされます。
- 削除は `/class remove weekday:月曜日 period:1`。
- 確認は `/class list`（画像の時間割表を DM で受信）、`/class table`（テキスト表）、`/class today`（今日の授業）。

**Web から**

1. Web ダッシュボードを開き、「Discord でログイン」します。
2. 上部の「前期／後期」を選びます。
3. 時間割表のセルをクリックし、科目名と教室を入力して保存します。

ログイン後のダッシュボードで変更した内容は、その場で Bot に反映されます。

### 4. 通知のタイミングを決める

```
/notify set notify_type:normal first:15 second:10
```

- `normal` は通常の授業、`exam` は試験期間の授業の設定です。
- 上の例では授業開始の 15 分前と 10 分前に DM が届きます。
- 朝の一覧通知の時刻は Web ダッシュボードの「通知・学期設定」で変更できます（初期値 08:00）。
- 自分の大学の時限の時刻に合わせるには `/setting period_time period:1 time:08:50` を使います。

### 5. 授業回数を設定する（任意）

```
/setting class_count term:前期 count:15
```

授業ごとに「2 回目の授業前通知」が送られた回数を数え、設定した回数に達するとその授業を時間割から削除し、通知を終了します。
回数を設定し直すと、その学期のカウントは 0 に戻ります。

### 6. 曜日・教室を一日だけ変える

| やりたいこと | Discord | Web ダッシュボード |
| --- | --- | --- |
| 祝日振替などで、ある日を別の曜日の時間割にする | `/class setday date:2026-11-04 new_weekday:月曜日` | 「時間割の上書き設定」→ 曜日上書き追加 →「上書き設定を保存する」 |
| ある日の特定の時限の教室を変える | `/class setroom date:2026-11-04 period:2 new_room:B201` | 「時間割の上書き設定」→ 教室上書き追加 →「上書き設定を保存する」 |

教室変更は授業前通知・朝の一覧・`/class today` に反映されます。同じ日・時限に両方ある場合は `/class setroom` の設定が優先です。

### 7. 補講・休講を登録する

**補講**：指定した日・時限に、登録済みの授業を 1 つ追加します。

```
/makeup add date:2026-11-07 period:3 source:月曜日 1限 線形代数 room:C301
```

- `date` は今日から 60 日先までが候補として表示されます（直接入力も可）。
- `source` は登録済みの授業から候補で選びます。
- `room` を省略すると元の授業の教室になります。
- 補講は通常の授業と同じように通知され、**元の授業の授業回数として数えます**。
- 同じ日・時限に、元の授業が異なる補講を複数登録できます（同じ授業の補講は 1 つまで）。

**休講**：指定した日の授業を 1 つ休講にします。

```
/cancel add date:2026-11-05 period:2
```

- 日付を入れてから `period` を選ぶと、その日にある授業だけが候補に出ます（曜日変更も反映）。
- 休講にした授業は**通知されず、授業回数にも数えません**。
- 朝の一覧と `/class today` には「【休講】」と表示されます。
- 休講にした時限に、別の授業の補講を入れることもできます。

**確認・取り消し**

```
/makeup list      /makeup remove target:（候補から選択）
/cancel list      /cancel remove target:（候補から選択）
```

**Web から**：ダッシュボードの「📝 補講・休講」で同じ操作ができます。追加・削除はその場で保存されます。

### 8. 試験期間の時間割を登録する

```
/exam create name:前期期末 start:2026-07-20 end:2026-07-31
/exam addclass name:前期期末 weekday:月曜日 period:2 subject:線形代数 room:大講義室 time:10:30
/exam set_time period:2 time:10:30
```

- 期間中は通常の時間割の代わりに試験時間割で通知します（通知タイミングは `exam` の設定）。
- `time` を指定すると、その試験だけ開始時刻を変えられます。`/exam set_time` は試験期間中の時限ごとの開始時刻です。
- `/exam list`・`/exam show`・`/exam removeclass`・`/exam delete` で確認・編集できます。

### 9. Gmail 連携（任意）

大学から届く休講・授業変更メールを Gmail から探して DM に表示します。読み取り専用で、メールの変更・送信はしません。

1. `/mail auth` を実行すると、Google の認証 URL が DM で届きます。
2. URL を開いて許可し、表示された認証コードをコピーします。
3. `/mail setcode code:（コード）` で連携が完了します。
4. `/mail fetch` で、直近 14 日間の件名に「休講・授業変更・曜日変更・振替」を含むメールを調べ、結果を DM で受け取ります。

- 認証 URL は 15 分間有効です。`/mail auth` を実行した本人しか、そのコードで連携できません。
- Web ダッシュボードからも「認証URLを取得」→ コードを貼り付け →「Gmail認証を保存する」で連携できます。

`/mail fetch` は結果を表示するだけで、時間割には自動で反映しません。必要に応じて `/cancel add` や `/class setday` で登録してください。

### 10. ログインせずに Web で入力して取り込む（連携キー）

ログイン不要の Web ページ（トップページ・試験ページ）では、入力内容はブラウザ内に下書きとして保存されます。これを Bot に取り込むには連携キーを使います。

1. Web ページで時間割や設定を入力します。
2. 「全体キーを発行」または機能別のキー（通常時間割・通知設定・上書き設定・Gmail 認証・試験設定）を押します。
3. 表示されたキーを Discord で `/web applykey key:XXXXXXXXXX` と実行します。

- キーは 10 文字・**1 回限り**・**24 時間有効**です。
- キーで取り込むと、その機能の既存データは Web の内容で置き換わります。
- Gmail 認証を含める場合は、同じページで「認証URLを取得」してから 15 分以内にキーを発行し、取り込んでください。

### よくあるつまずき

| 症状 | 対処 |
| --- | --- |
| DM が届かない | Discord の DM 受信設定を確認し、もう一度コマンドを実行する |
| 通知が来ない | `/class status` で学期を確認する。学期の期間外だと通知されない。休講登録していないかも確認する |
| 登録したはずの授業が見えない | 前期／後期の切り替え先を確認する |
| `/web applykey` で「無効」と出る | キーは 1 回限り・24 時間有効。Web 側で再発行する |

---

## コマンド一覧

すべてのコマンドはサーバーのチャンネルと Bot との DM のどちらでも使えます。返信は本人にしか見えないか、DM で届きます。

### `/class` 授業管理

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/class add` | `weekday` `period` `subject` `room` | 現在の学期に授業を登録（同じ曜日・時限は上書き） |
| `/class remove` | `weekday` `period` | 授業を削除 |
| `/class list` | `[term]` | 時間割表の画像を DM で送信 |
| `/class table` | `[term]` | テキストの時間割表を表示 |
| `/class today` | — | 今日の授業一覧（補講・休講・曜日変更・教室変更を反映） |
| `/class setday` | `date` `new_weekday` | 指定日を別の曜日の時間割にする（前期・後期の全授業に適用） |
| `/class setroom` | `date` `period` `new_room` | 指定日・時限の教室を変更 |
| `/class status` | — | システムが判定している現在の学期を表示 |

### `/makeup` 補講

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/makeup add` | `date` `period` `source` `[room]` | 指定日・時限に登録済みの授業を補講として追加 |
| `/makeup remove` | `target` | 補講を削除 |
| `/makeup list` | — | 補講一覧 |

### `/cancel` 休講

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/cancel add` | `date` `period` | 指定日の授業を休講にする |
| `/cancel remove` | `target` | 休講を取り消す |
| `/cancel list` | — | 休講一覧 |

### `/exam` 試験時間割

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/exam create` | `name` `start` `end` | 試験時間割を作成 |
| `/exam addclass` | `name` `weekday` `period` `subject` `room` `[time]` | 試験時間割に授業を追加 |
| `/exam removeclass` | `name` `weekday` `period` | 試験時間割から授業を削除 |
| `/exam list` | — | 試験時間割の一覧 |
| `/exam show` | `name` | 試験時間割の中身 |
| `/exam delete` | `name` | 試験時間割を削除 |
| `/exam set_time` | `period` `time` | 試験期間中の時限の開始時刻 |

### `/notify` 通知

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/notify set` | `notify_type`（`normal`／`exam`） `first` `second` | 授業前通知を何分前に送るか |
| `/notify show` | — | 現在の通知設定 |
| `/notify set_period` | `period` `time` | 時限の開始時刻（`/setting period_time` と同じ） |

### `/setting` 設定

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/setting period_time` | `period` `time` | 時限の開始時刻を設定 |
| `/setting reset_period` | `period`（`all` で全時限） | 時限の開始時刻を初期値に戻す |
| `/setting term_range` | `term` `start_date` `end_date` | 学期の通知期間 |
| `/setting class_count` | `term` `count` | 学期の授業回数（設定するとカウントは 0 に戻る） |
| `/setting show` | — | 現在の設定を DM で表示 |

### `/mail` Gmail 連携

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/mail auth` | — | Gmail 認証 URL を DM で送信 |
| `/mail setcode` | `code` | 認証コードを登録して連携を完了 |
| `/mail fetch` | — | 休講・授業変更メールを探して DM に表示（保存はしない） |

### その他

| コマンド | 引数 | 内容 |
| --- | --- | --- |
| `/web applykey` | `key` | Web で発行した連携キーを取り込む |
| `/help` | — | コマンド一覧を DM で送信 |

---

## 仕様

### 時限と開始時刻

初期値は次のとおりです。`/setting period_time` で利用者ごとに変更できます。

| 時限 | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| 開始 | 09:00 | 10:45 | 13:15 | 15:00 | 16:45 | 18:25 |

開始時刻は次の優先順で決まります。

- 通常時：`/setting period_time` の設定 → 授業に保存された時刻 → 初期値
- 試験期間：`/exam set_time` の設定 → `/setting period_time` の設定 → 試験授業の `time` → 初期値

### 学期の判定

- 学期は「前期」「後期」の 2 つで、時間割・試験時間割は学期ごとに別々に保存します。
- 現在の学期は、`term_ranges`（学期の期間）に今日が含まれる学期です。どちらにも含まれなければ月で判定します（4〜9 月が前期）。
- 判定結果は毎朝 8:00 を境に 1 日 1 回更新し、利用者データにキャッシュします。
- 補講・休講の学期は、登録する日付から判定します。

### 授業前通知

- Bot は 1 秒ごとに全利用者のデータを確認します（タイムゾーンは `APP_TIMEZONE`、初期値 `Asia/Tokyo`）。
- その日の授業ごとに「開始時刻 − 通知分数」の時刻を過ぎたら DM を送ります。Bot が止まっていた場合に備え、予定時刻から 5 分（`NOTIF_GRACE_WINDOW` 秒）以内なら遅れて送ります。
- 同じ授業・同じ通知は 1 日 1 回だけ送ります。送信済みの記録は `sent_reminders.json` に当日分だけ保存するので、Bot を再起動しても同じ通知を再送したり、授業回数を二重に数えたりしません。
- 学期の期間を設定していて、今日がどちらの学期の期間にも入らない場合、その利用者には何も送りません。
- 初期値：通常 15 分前・10 分前、試験 30 分前・25 分前。

### その日の授業の決まり方

1. 曜日変更（`/class setday`）があればその曜日、なければ今日の曜日の授業を取り出します。
2. 試験期間中なら、通常の時間割の代わりにその試験時間割の授業を使います。
3. 休講登録された時限の授業を除きます。
4. その日の補講を加えます。

### 朝の一覧通知

- 毎日 `morning_notice_time`（初期値 08:00）に、その日の授業一覧を DM で 1 回送ります。補講は「【補講】」、休講は「【休講】」と表示します。
- 送信済みかどうかは `morning_sent.json` に記録します。Bot を 8:00〜12:00 の間に起動した場合は、起動時に「8:00 時点の処理」を 1 回行うため、朝の通知時刻が 08:00 の利用者には遅れて一覧が届きます。

### 授業回数のカウント

- 通常期間中、各授業の **2 回目の授業前通知を送ったとき** に 1 回と数えます（試験期間はカウントしません）。
- 補講は元の授業（曜日・時限・科目）の回数として数えます。
- 休講の授業は通知されないため数えません。
- 学期の授業回数（`class_count_targets`）に達すると、その授業を時間割から削除し、DM で知らせます。

### 補講・休講

| | 補講 | 休講 |
| --- | --- | --- |
| 指定するもの | 日付・時限・元の授業（曜日と時限）・教室（任意） | 日付・時限 |
| 対象 | その学期に登録済みの授業 | その日に実際にある通常授業（曜日変更を反映） |
| 重複 | 同じ日・時限に、元の授業が異なれば複数可 | 同じ日・時限に 1 件（その枠の授業を休みにする） |
| 通知 | 通常の授業と同様に通知 | 通知しない |
| 授業回数 | 元の授業の回数に加算 | 数えない |
| 試験期間中 | 試験時間割に加えて通知 | 試験時間割の授業にも適用 |

休講は通常の授業（試験期間中は試験の授業）に適用し、補講には適用しません。休講の時限に補講を入れると、補講だけが通知されます。

### 試験期間

- 試験時間割は名前・開始日・終了日・授業のリストを持ち、学期ごとに保存します。
- 今日がいずれかの試験時間割の期間内なら試験モードになり、その時間割の授業で通知します。DM には「【試験期間】」と付きます。

### Web 連携キー

- Web で入力した内容を JSON にまとめて `web_link_keys.json` に保存し、10 文字のキー（英大文字と数字）を発行します。
- キーは 24 時間有効で、`/web applykey` で 1 回使うと削除されます。
- 機能別キーは、その機能に関するデータだけを取り込みます。「全体」は前期・後期両方の時間割を置き換えます。
- 授業回数の目標を取り込むと、対象学期のカウントは 0 に戻ります。

### Gmail 認証

- Google OAuth の PKCE を使います。認証 URL を発行するとき、検証用の値（`code_verifier`）をサーバーのメモリに 15 分間保持し、コードを交換するときに照合します。そのため、第三者が他人の認証コードを手に入れても、自分のアカウントに紐付けることはできません。
- `/mail auth` とログイン後のダッシュボードで発行した認証は、その利用者に紐付きます。ほかの利用者はそのコードを使えません。
- ログイン不要ページで発行した認証は `state` で識別します。ページは `state` を下書きに保存し、連携キーに含めて送ります。`state` のない古い形式のキーでは、Gmail 連携を行いません。
- 同じ利用者が認証 URL を発行し直すと、古い URL は無効になります。Bot を再起動した場合も、発行済みの認証は無効になります。
- 認証コードはサーバーに保存しません。トークンだけを `gmail_tokens/` に保存します。

### データの保存

データは利用者ごとに作業ディレクトリの `user_<DiscordユーザーID>.json` に保存します（データベースは使いません）。主なキーは次のとおりです。

| キー | 内容 |
| --- | --- |
| `classes_by_term` | 学期ごとの時間割。`day`（0=月〜6=日）・`period`・`subject`・`room`、日付ごとの曜日変更 `overrides`・教室変更 `room_overrides` |
| `exam_schedules_by_term` | 学期ごとの試験時間割 |
| `makeup_classes` | 補講。`date`・`period`・`subject`・`room`・元の授業の `day` と `source_period`・`term` |
| `cancelled_classes` | 休講。`date`・`period`・`subject`・`term` |
| `notify_settings` | 通知タイミング（`normal`／`exam` それぞれ `first`・`second`） |
| `morning_notice_time` | 朝の一覧通知の時刻 |
| `period_overrides` / `exam_period_overrides` | 時限の開始時刻（通常／試験） |
| `term_ranges` | 学期の期間 |
| `class_count_targets` / `class_attendance_count` | 授業回数の目標と現在のカウント |
| `day_overrides` / `room_overrides` | Web で設定した曜日・教室上書きの一覧 |
| `effective_term_cache` | 学期判定のキャッシュ |

その他のファイル：

| ファイル | 内容 |
| --- | --- |
| `gmail_tokens/user_<ID>.pickle` | Gmail の認証トークン |
| `web_link_keys.json` | 発行済みの連携キー |
| `morning_sent.json` | 朝の一覧通知の送信記録 |
| `sent_reminders.json` | 授業前通知の送信記録（当日分のみ） |
| `logs/` | ログ |

これらは `.gitignore` で Git の管理対象外です（`*.json`）。

---

## Web ダッシュボードと Web API

Bot と同じプロセスで FastAPI の Web サーバー（初期値ポート 8000）が動き、同じ利用者データを読み書きします。

### ページ

| URL | 内容 |
| --- | --- |
| `/` | ログイン不要の時間割入力ページ（下書き＋連携キー発行） |
| `/exam.html` | ログイン不要の試験時間割入力ページ |
| `/guide.html` | 使い方ガイド |
| `/login` | Discord ログイン |
| `/dashboard` | ログイン後のダッシュボード（変更は即時反映） |
| `/privacy.html` / `/terms.html` | プライバシーポリシー／利用規約 |

`docs/` は GitHub Pages 公開用の静的ページのコピーです。

### セキュリティ

- **外部スクリプトを使わない**：Tailwind は版を固定して `web/vendor/`（GitHub Pages 用は `docs/vendor/`）に置き、自サーバーから配信します。
- **セキュリティヘッダー**：すべてのレスポンスに次のヘッダーを付けます。
  - `Content-Security-Policy`：スクリプト・通信・画像などを自オリジンに限定し、iframe への埋め込みを禁止
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - インラインスクリプトがあるため、CSP の `script-src` には `'unsafe-inline'` を含みます。
- **HTTPS**：プロキシ（Cloudflare Tunnel など）経由で HTTP（`X-Forwarded-Proto: http`）のアクセスが来たら、HTTPS へ 308 でリダイレクトします。HTTPS のアクセスには `Strict-Transport-Security: max-age=31536000` を付けます。`localhost` への直接アクセスは HTTP のまま使えます。
- **API ドキュメント**：`/docs`・`/redoc`・`/openapi.json` は、環境変数 `DEBUG=true` のときだけ公開します。
- **ログイン画面のデバッグ表示**：`/login` と `/auth/callback` の画面上のデバッグ表示は、URL に `?debug=1` を付けたときだけ出します。URL 由来の値は HTML として解釈しません。

### ログイン

Discord OAuth2（スコープ `identify` のみ）でログインし、サーバーが 30 日有効の JWT を発行します。ダッシュボードは JWT を `Authorization: Bearer` ヘッダーで送ります。

### API（ログインが必要：`Authorization: Bearer <JWT>`）

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/auth/url` | Discord ログイン URL（認証不要） |
| POST | `/api/auth/login?code=` | 認可コードで JWT を発行（認証不要） |
| GET | `/api/auth/me` | ログイン中の利用者 |
| GET / POST | `/api/classes?term=` | 授業一覧／追加 |
| PUT / DELETE | `/api/classes/{weekday}/{period}?term=` | 授業の更新／削除 |
| POST | `/api/classes/bulk?term=` | 時間割の一括保存 |
| GET | `/api/classes/table?term=` | 曜日×時限の表形式 |
| GET / POST | `/api/exams` | 試験時間割の一覧／作成 |
| GET / DELETE | `/api/exams/{name}` | 試験時間割の取得／削除 |
| POST / DELETE | `/api/exams/{name}/classes` | 試験時間割の授業の追加／削除 |
| GET / POST | `/api/makeup` | 補講の一覧／追加 |
| DELETE | `/api/makeup/{date}/{period}?source_weekday=&source_period=` | 補講の削除（同じ枠に複数あるときは元の授業を指定） |
| GET / POST | `/api/cancel` | 休講の一覧／追加 |
| GET | `/api/cancel/candidates?date=` | 指定日の休講候補（その日の通常授業） |
| DELETE | `/api/cancel/{date}/{period}` | 休講の取り消し |
| POST | `/api/overrides/bulk` | 曜日・教室上書きの一括保存 |
| GET / PUT | `/api/settings` | 設定の取得／通知設定の更新 |
| POST | `/api/settings/period-time` | 時限の開始時刻 |
| POST | `/api/settings/term` | 学期の期間と授業回数 |
| GET | `/api/settings/gmail/auth-url` | ログイン中の利用者に紐付けた Gmail 認証 URL と `state` |
| POST | `/api/settings/gmail` | Gmail 認証コード（と `state`）をトークンに交換して連携を完了 |

補講の追加（`POST /api/makeup`）の例：

```json
{
  "date": "2026-11-07",
  "period": "3",
  "source_weekday": "月曜日",
  "source_period": "1",
  "room": "C301",
  "term": "後期"
}
```

`room` と `term` は省略できます（`term` 省略時は日付から判定）。休講の追加（`POST /api/cancel`）は `{"date": "2026-11-05", "period": "2"}` です。
入力が不正な場合は 400 とエラーメッセージを返します。データを変更すると、利用者に DM で通知します。

### API（認証不要）

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/health` | ヘルスチェック |
| GET | `/api/public/gmail-auth-url` | Gmail 認証 URL と `state`（ログイン不要ページ用） |
| POST | `/api/public/issue-link-key` | 連携キーの発行 |

### API（API キーが必要：`X-API-Key` ヘッダー、旧形式）

`/api/user/{user_id}/classes`、`/classes/upsert`、`/classes/{weekday}/{period}`、`/schedule-table`、`/all-data`、`/api/constants`。
授業を扱うエンドポイントは `?term=前期` のように学期を指定でき、省略時は現在の学期を対象にします。
詳しくは [README_WEB_API.md](README_WEB_API.md) を参照してください。

---

## セットアップと運用

### 必要なもの

- Python 3.11 以上（Docker で動かす場合は不要）
- Discord Bot のトークン（[Discord Developer Portal](https://discord.com/developers/applications)）
- Web ログインを使う場合：同じアプリケーションの OAuth2 クライアント ID・シークレットとリダイレクト URL の登録
- Gmail 連携を使う場合：Google Cloud の OAuth クライアント（`client_secret*.json`）

ファイルのロックに `fcntl` を使うため、Linux（Docker を含む）での実行を前提にしています。

### 環境変数（`.env`）

| 変数 | 必須 | 内容 |
| --- | --- | --- |
| `DISCORD_TOKEN` | ✅ | Bot トークン |
| `GUILD_ID` | | 指定するとそのサーバーにもコマンドを同期 |
| `SYNC_GUILD_COMMANDS` | | `1` でギルド同期を有効化 |
| `APP_TIMEZONE` | | 通知のタイムゾーン（初期値 `Asia/Tokyo`） |
| `NOTIF_GRACE_WINDOW` | | 通知の遅延許容秒数（初期値 300） |
| `WEB_HOST` / `WEB_PORT` | | Web サーバーの待受（初期値 `0.0.0.0` / `8000`） |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | Web ログイン時 | Discord OAuth2 |
| `DISCORD_REDIRECT_URI` | | 初期値は `TUNNEL_PUBLIC_BASE_URL/auth/callback`、なければ `http://localhost:8000/auth/callback` |
| `SECRET_KEY` | Web ログイン時 | JWT の署名鍵（必ず変更する） |
| `TUNNEL_PUBLIC_BASE_URL` | | 公開 URL（Cloudflare Tunnel など）。CORS とリダイレクト URL に使用 |
| `GITHUB_PAGES_URL` | | GitHub Pages の URL（ログ表示用） |
| `CORS_ORIGIN_GITHUB_PAGES` | | 追加で許可するオリジン（カンマ区切り） |
| `CORS_ORIGIN_REGEX` | | 許可するオリジンの正規表現（初期値 `*.github.io`） |
| `API_KEY` | 旧 API 使用時 | `X-API-Key` で照合するキー |
| `GMAIL_CREDENTIALS` | | Google OAuth クライアント JSON のパス（未指定時は `client_secret*.json` などを自動検出） |
| `GMAIL_REDIRECT_URI` | | Gmail 認証のリダイレクト先（初期値 `https://ninigi05.github.io/oauth-redirect/`） |
| `DEBUG` | | `true` で `/docs`・`/redoc`・`/openapi.json` を公開（本番では設定しない） |
| `DISCORD_FORCE_IPV4` / `AIOHTTP_DNS_TTL` | | Discord 接続の調整（初期値 `1` / `60`） |
| `TUNNEL_TOKEN` | Docker 時 | Cloudflare Tunnel のトークン |

### ローカルで起動する

```bash
pip install -r requirements.txt
python check_setup.py   # 環境変数とファイルを確認（任意）
python main.py          # Bot と Web サーバーを同時に起動
```

### Docker で起動する

```bash
docker compose up -d --build
```

Bot と Web サーバー（ポート 8000）のコンテナと、Cloudflare Tunnel のコンテナが起動します。リポジトリのディレクトリをコンテナの `/app` にマウントするので、利用者データはホスト側に残ります。

### Windows で Quick Tunnel を自動起動する

`scripts/register_startup_task.ps1` で、PC 起動時に Docker と Cloudflare Quick Tunnel を立ち上げるタスクを登録できます。手順は [docs/quick_tunnel_windows.md](docs/quick_tunnel_windows.md) を参照してください。

### Bot に必要な権限

- Developer Portal で **Server Members Intent** と **Message Content Intent** を有効にしてください（Bot が両方を要求します）。
- サーバーへの参加時に新規メンバーへ DM を送ってキックするため、**メンバーをキックする権限**が必要です。
- コマンドはユーザーインストールにも対応しています（サーバーに入れなくても DM で使えます）。

---

## ファイル構成

```
main.py                  Bot 本体（起動・/help・/mail・/web・新規メンバー対応）。Web サーバーも起動
utils.py                 データ読み書き、学期判定、補講・休講の共通処理、定数
cogs/
  class_cog.py           /class
  makeup_cog.py          /makeup, /cancel
  exam_cog.py            /exam
  notification_cog.py    授業前通知・朝の一覧・授業回数カウント、/notify
  setting_cog.py         /setting
web_api_integration.py   Bot と一緒に動く FastAPI アプリ（ページ配信・公開 API・旧 API）
web_link_service.py      連携キーの発行・消費
gmail_oauth.py           Gmail 認証（PKCE）の URL 発行・コード交換
api_security.py          旧 API の API キー認証
web/
  api/                   ダッシュボード用 API（classes, exams, makeup, settings, notification）
  auth.py, config.py     Discord OAuth2 と JWT
  schemas.py             API のリクエスト／レスポンス定義
  templates/             ログイン・コールバック・ダッシュボード
  *.html                 ログイン不要のページ
  vendor/                版を固定した Tailwind（自サーバーから配信）
  app.py                 単体起動用の FastAPI アプリ（main.py からは使われない）
docs/                    GitHub Pages 用の静的ページ
scripts/                 Windows 用 Quick Tunnel 自動起動スクリプト
```
