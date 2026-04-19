/**
 * Amazonラベルツール v2.0 ライセンス管理スクリプト
 *
 * 【運用フロー】
 * 1. ユーザーがGoogleフォームで氏名・電話番号・メールアドレスを登録
 * 2. フォーム送信時にこのスクリプトが自動でライセンスキーを発行
 * 3. スプシに記録（有効=TRUE で登録）
 * 4. 登録者へ自動でライセンスキーをメール送信
 * 5. 管理者は「有効」列のチェックボックスで利用停止/再開を切替
 *
 * 【初回セットアップ】
 * 1. Googleフォームを作成（質問：氏名、電話番号、メールアドレス）
 * 2. フォームの回答先としてスプレッドシートを作成
 * 3. スプレッドシートで「拡張機能 → Apps Script」を開く
 * 4. このファイルの中身を貼り付けて保存
 * 5. setupSheet() を1回実行（列ヘッダーを自動整形）
 * 6. installFormTrigger() を1回実行（フォーム送信トリガーを登録）
 * 7. 「ファイル → 共有 → ウェブに公開」で「CSV形式」「シート1」で公開URLを取得
 * 8. その公開URLを config.json の license_csv_url に設定
 */

// ========== 設定 ==========

// スプシに出力する列（順番固定）
const COL_TIMESTAMP   = 1;  // A: タイムスタンプ（フォーム自動）
const COL_NAME        = 2;  // B: 氏名（フォーム自動）
const COL_PHONE       = 3;  // C: 電話番号（フォーム自動）
const COL_EMAIL       = 4;  // D: メールアドレス（フォーム自動）
const COL_LICENSE_KEY = 5;  // E: ライセンスキー（このスクリプトが自動発行）
const COL_ACTIVE      = 6;  // F: 有効（TRUE/FALSE チェックボックス）
const COL_NOTE        = 7;  // G: 備考（任意）

// メール件名・本文
const MAIL_SUBJECT = 'Amazonラベルツール v2.0 ライセンス発行のお知らせ';
const MAIL_FROM_NAME = 'Amazonラベルツール サポート';


// ========== 初回セットアップ ==========

/**
 * スプシの列ヘッダーを整える（1回だけ実行）
 */
function setupSheet() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  const headers = [
    'タイムスタンプ',
    '氏名',
    '電話番号',
    'メールアドレス',
    'ライセンスキー',
    '有効',
    '備考',
  ];

  // 既存のヘッダーを確認
  const lastCol = sheet.getLastColumn();
  if (lastCol < headers.length) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  } else {
    // ライセンスキー・有効・備考 列だけ追加（既存のフォーム列は触らない）
    const existingHeaders = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
    for (let i = 0; i < headers.length; i++) {
      if (!existingHeaders[i]) {
        sheet.getRange(1, i + 1).setValue(headers[i]);
      }
    }
  }

  // 「有効」列をチェックボックス形式に
  const activeCol = sheet.getRange(2, COL_ACTIVE, Math.max(sheet.getMaxRows() - 1, 1), 1);
  const rule = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  activeCol.setDataValidation(rule);

  SpreadsheetApp.getUi().alert('列ヘッダーを整備しました。');
}


/**
 * フォーム送信トリガーを登録（1回だけ実行）
 */
function installFormTrigger() {
  // 既存トリガーを削除
  const triggers = ScriptApp.getProjectTriggers();
  for (const t of triggers) {
    if (t.getHandlerFunction() === 'onFormSubmit') {
      ScriptApp.deleteTrigger(t);
    }
  }

  // 新規トリガー登録
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ScriptApp.newTrigger('onFormSubmit')
    .forSpreadsheet(ss)
    .onFormSubmit()
    .create();

  SpreadsheetApp.getUi().alert('フォーム送信トリガーを登録しました。');
}


// ========== フォーム送信時処理 ==========

/**
 * フォーム送信時に自動実行される関数
 */
function onFormSubmit(e) {
  const sheet = e.range.getSheet();
  const row = e.range.getRow();

  // 入力値を取得
  const email = String(sheet.getRange(row, COL_EMAIL).getValue()).trim();
  const name = String(sheet.getRange(row, COL_NAME).getValue()).trim();

  if (!email) {
    return;
  }

  // 重複チェック（既存のメールアドレスは上書きしない）
  const existingKey = findExistingLicenseKey(sheet, email, row);
  let licenseKey;
  if (existingKey) {
    licenseKey = existingKey;
  } else {
    licenseKey = generateLicenseKey();
  }

  // スプシに記録
  sheet.getRange(row, COL_LICENSE_KEY).setValue(licenseKey);
  sheet.getRange(row, COL_ACTIVE).setValue(true);

  // ライセンスキーをメール送信
  sendLicenseMail(email, name, licenseKey);
}


/**
 * 既存のメールアドレスに紐づくライセンスキーを探す
 */
function findExistingLicenseKey(sheet, email, currentRow) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;

  const data = sheet.getRange(2, COL_EMAIL, lastRow - 1, COL_LICENSE_KEY - COL_EMAIL + 1).getValues();
  for (let i = 0; i < data.length; i++) {
    const rowIdx = i + 2;
    if (rowIdx === currentRow) continue;
    const rowEmail = String(data[i][0]).trim().toLowerCase();
    const rowKey = String(data[i][COL_LICENSE_KEY - COL_EMAIL]).trim();
    if (rowEmail === email.toLowerCase() && rowKey) {
      return rowKey;
    }
  }
  return null;
}


/**
 * ライセンスキー生成（UUID v4 ベース・大文字16桁×2）
 */
function generateLicenseKey() {
  const uuid = Utilities.getUuid().replace(/-/g, '').toUpperCase();
  // 8-4-4-4桁フォーマット（計20文字）
  return uuid.substring(0, 8) + '-' +
         uuid.substring(8, 12) + '-' +
         uuid.substring(12, 16) + '-' +
         uuid.substring(16, 20);
}


/**
 * ライセンスキーをメール送信
 */
function sendLicenseMail(email, name, licenseKey) {
  const body = [
    (name ? name + ' 様' : 'お客様'),
    '',
    'この度はAmazonラベルツール v2.0 にご登録いただき、誠にありがとうございます。',
    'ライセンスキーを発行しましたので、下記をご確認ください。',
    '',
    '【ライセンスキー】',
    licenseKey,
    '',
    '【ご利用方法】',
    '1. ツールを起動すると「ライセンス認証」画面が表示されます',
    '2. ご登録のメールアドレスと、上記のライセンスキーを入力してください',
    '3. 認証が完了するとご利用いただけます',
    '',
    '※ライセンスキーは他の方には絶対に教えないでください。',
    '※本メールへの返信はできません。ご不明点は販売元までご連絡ください。',
    '',
  ].join('\n');

  MailApp.sendEmail({
    to: email,
    subject: MAIL_SUBJECT,
    body: body,
    name: MAIL_FROM_NAME,
  });
}


// ========== テスト・デバッグ用 ==========

/**
 * 手動テスト：指定行に対してライセンスキーを発行
 */
function manuallyIssueLicenseForRow() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  const row = sheet.getActiveCell().getRow();
  if (row < 2) {
    SpreadsheetApp.getUi().alert('データ行を選択してから実行してください。');
    return;
  }

  const email = String(sheet.getRange(row, COL_EMAIL).getValue()).trim();
  const name = String(sheet.getRange(row, COL_NAME).getValue()).trim();

  if (!email) {
    SpreadsheetApp.getUi().alert('メールアドレスが空です。');
    return;
  }

  const existingKey = findExistingLicenseKey(sheet, email, row);
  const licenseKey = existingKey || generateLicenseKey();

  sheet.getRange(row, COL_LICENSE_KEY).setValue(licenseKey);
  sheet.getRange(row, COL_ACTIVE).setValue(true);

  sendLicenseMail(email, name, licenseKey);
  SpreadsheetApp.getUi().alert('ライセンスキーを発行してメール送信しました。\n' + licenseKey);
}
