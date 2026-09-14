from html import escape

from django.conf import settings

NAVY = "#173968"
ORANGE = "#f7941d"
PAGE_BG = "#eef2f7"
CARD_BG = "#ffffff"
MUTED = "#5b6b82"
LINE = "#e4eaf2"


def build_email_verification_message(*, code: str) -> tuple[str, str, str]:
    minutes = settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES
    safe_code = escape(code)
    subject = "Doğrulama kodunuz / Your verification code"

    text_body = (
        "Benim Depom\n\n"
        "Kayıt doğrulama\n"
        "Hesabınızı oluşturmak için bu kodu uygulamaya girin.\n"
        f"Doğrulama kodunuz: {code}\n"
        f"Bu kod {minutes} dakika geçerlidir.\n\n"
        "Registration verification\n"
        "Enter this code in the app to finish creating your account.\n"
        f"Your verification code: {code}\n"
        f"This code is valid for {minutes} minutes.\n\n"
        "Bu e-postayı siz istemediyseniz dikkate almayın.\n"
        "If you did not request this email, you can ignore it."
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="tr">
  <body style="margin:0;padding:0;background:{PAGE_BG};font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE_BG};padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:{CARD_BG};border-radius:16px;overflow:hidden;border:1px solid {LINE};">
            <tr>
              <td style="background:{NAVY};padding:22px 28px;">
                <p style="margin:0;color:{ORANGE};font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;">
                  Benim Depom
                </p>
                <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;line-height:1.3;font-weight:800;">
                  Doğrulama kodunuz<br>
                  <span style="font-size:16px;font-weight:600;color:#d7e2f2;">Your verification code</span>
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:28px;">
                <p style="margin:0 0 6px;color:{NAVY};font-size:14px;font-weight:800;">
                  Kayıt doğrulama
                </p>
                <p style="margin:0 0 18px;color:{MUTED};font-size:14px;line-height:1.5;">
                  Hesabınızı oluşturmak için bu kodu uygulamaya girin.
                </p>
                <p style="margin:0 0 6px;color:{NAVY};font-size:14px;font-weight:800;">
                  Registration verification
                </p>
                <p style="margin:0 0 22px;color:{MUTED};font-size:14px;line-height:1.5;">
                  Enter this code in the app to finish creating your account.
                </p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td align="center" style="background:#f7f9fc;border:1px solid {LINE};border-radius:12px;padding:18px 16px;">
                      <p style="margin:0 0 8px;color:{MUTED};font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;">
                        Kod / Code
                      </p>
                      <p style="margin:0;color:{NAVY};font-size:32px;line-height:1;font-weight:800;letter-spacing:0.18em;">
                        {safe_code}
                      </p>
                    </td>
                  </tr>
                </table>
                <p style="margin:20px 0 0;color:{MUTED};font-size:13px;line-height:1.5;">
                  Bu kod {minutes} dakika geçerlidir.<br>
                  This code is valid for {minutes} minutes.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 24px;">
                <p style="margin:0;padding-top:16px;border-top:1px solid {LINE};color:{MUTED};font-size:12px;line-height:1.5;">
                  Bu e-postayı siz istemediyseniz dikkate almayın.<br>
                  If you did not request this email, you can ignore it.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    return subject, text_body, html_body
