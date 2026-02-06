WELCOME_COURSE_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Welcome to your course</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f9fc;">
    <!-- Preheader (hidden preview text) -->
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      Your Moodle account is ready. Set your password and start learning.
    </div>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f6f9fc;">
      <tr>
        <td align="center" style="padding:28px 12px;">
          <!-- Container -->
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px;max-width:600px;">
            

            <tr>
              <td style="background:#ffffff;border:1px solid #e6ebf1;border-radius:14px;overflow:hidden;">
                <!-- Header -->
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr>
                    <td style="padding:26px 26px 10px 26px;">
                      <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:12px;font-weight:700;color:#6b7c93;text-transform:uppercase;letter-spacing:0.08em;">
                        WELCOME
                      </div>
                      <div style="margin-top:6px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:22px;font-weight:800;color:#0a2540;letter-spacing:-0.02em;line-height:1.25;">
                        Welcome to <span style="color:#2b6cee;">{{brand_name}}</span>
                      </div>
                      <div style="margin-top:10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:14px;font-weight:500;color:#425466;line-height:1.6;">
                        Your enrollment is confirmed. We’ve created your Moodle
                      </div>
                    </td>
                  </tr>
                </table>

                <!-- Divider -->
                <div style="height:1px;background:#e6ebf1;"></div>

                <!-- Steps -->
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr>
                    <td style="padding:22px 26px 8px 26px;">
                      <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:14px;font-weight:800;color:#0a2540;letter-spacing:-0.01em;">
                        How to access your course
                      </div>
                      <div style="margin-top:6px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:13px;font-weight:500;color:#425466;line-height:1.6;">
                        For security, we don’t send passwords by email. You’ll set your password directly in Moodle.
                      </div>
                    </td>
                  </tr>

                  <tr>
                    <td style="padding:0 26px 22px 26px;">
                      <!-- Step 1 -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:12px;">
                        <tr>
                          <td valign="top" style="width:34px;">
                            <div style="width:26px;height:26px;border-radius:999px;background:#eef2ff;color:#3730a3;
                              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:12px;font-weight:800;line-height:26px;text-align:center;">
                              1
                            </div>
                          </td>
                          <td style="padding-left:10px;">
                            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:800;color:#0a2540;">
                              Go to your Moodle login
                            </div>
                            <div style="margin-top:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:500;color:#425466;line-height:1.55;">
                              Open Moodle using this link:
                              <br />
                              <a href="{{moodle_login_url}}" style="color:#2b6cee;text-decoration:none;font-weight:700;">
                                {{moodle_login_url}}
                              </a>
                            </div>
                          </td>
                        </tr>
                      </table>

                      <!-- Step 2 -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:14px;">
                        <tr>
                          <td valign="top" style="width:34px;">
                            <div style="width:26px;height:26px;border-radius:999px;background:#eef2ff;color:#3730a3;
                              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:12px;font-weight:800;line-height:26px;text-align:center;">
                              2
                            </div>
                          </td>
                          <td style="padding-left:10px;">
                            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:800;color:#0a2540;">
                              Click "Lost password?”
                            </div>
                            <div style="margin-top:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:500;color:#425466;line-height:1.55;">
                              On the login page, select <strong>Lost password?</strong>.
                            </div>
                          </td>
                        </tr>
                      </table>

                      <!-- Step 3 -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:14px;">
                        <tr>
                          <td valign="top" style="width:34px;">
                            <div style="width:26px;height:26px;border-radius:999px;background:#eef2ff;color:#3730a3;
                              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:12px;font-weight:800;line-height:26px;text-align:center;">
                              3
                            </div>
                          </td>
                          <td style="padding-left:10px;">
                            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:800;color:#0a2540;">
                              Enter your email in section "Search by email address" and click "Search"
                            </div>
                            <div style="margin-top:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:500;color:#425466;line-height:1.55;">
                              Use the same email you used to purchase:
                              <br />
                              <strong style="color:#0a2540;">{{buyer_email}}</strong>
                            </div>
                          </td>
                        </tr>
                      </table>

                      <!-- Step 4 -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:14px;">
                        <tr>
                          <td valign="top" style="width:34px;">
                            <div style="width:26px;height:26px;border-radius:999px;background:#eef2ff;color:#3730a3;
                              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:12px;font-weight:800;line-height:26px;text-align:center;">
                              4
                            </div>
                          </td>
                          <td style="padding-left:10px;">
                            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:800;color:#0a2540;">
                              Create your new password
                            </div>
                            <div style="margin-top:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:500;color:#425466;line-height:1.55;">
                              Moodle will email you a secure password reset link. Open it and set your password.
                            </div>
                          </td>
                        </tr>
                      </table>

                      <!-- Step 5 -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:14px;">
                        <tr>
                          <td valign="top" style="width:34px;">
                            <div style="width:26px;height:26px;border-radius:999px;background:#eef2ff;color:#3730a3;
                              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:12px;font-weight:800;line-height:26px;text-align:center;">
                              5
                            </div>
                          </td>
                          <td style="padding-left:10px;">
                            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:800;color:#0a2540;">
                              Log in and open your course
                            </div>
                            <div style="margin-top:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                              font-size:13px;font-weight:500;color:#425466;line-height:1.55;">
                              Return to Moodle and log in with:
                              <br />
                              <span style="display:inline-block;margin-top:6px;">
                                <strong style="color:#0a2540;">Email:</strong> {{buyer_email}}
                              </span>
                              <br />
                              <span style="display:inline-block;margin-top:2px;">
                                <strong style="color:#0a2540;">Password:</strong> the one you just created
                              </span>
                              <br /><br />
                            </div>
                          </td>
                        </tr>
                      </table>

                      <!-- CTA -->
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:18px;">
                        <tr>
                          <td>
                            <a href="{{moodle_login_url}}"
                              style="display:inline-block;background:#2b6cee;color:#ffffff;text-decoration:none;
                                padding:12px 16px;border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                                font-size:13px;font-weight:800;">
                              Open Moodle
                            </a>
                          </td>
                        </tr>
                      </table>

                      <!-- Help -->
                      <div style="margin-top:16px;padding:12px 14px;border:1px solid #e6ebf1;background:#f6f9fc;border-radius:12px;
                        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:12px;font-weight:600;color:#425466;line-height:1.6;">
                        If you don’t receive the reset email within <strong>5 minutes</strong>, check your Spam/Junk folder
                        or contact us at
                        <a href="mailto:{{support_email}}" style="color:#2b6cee;text-decoration:none;font-weight:800;">
                          {{support_email}}
                        </a>.
                      </div>

                    </td>
                  </tr>
                </table>

                <!-- Footer -->
                <div style="height:1px;background:#e6ebf1;"></div>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr>
                    <td style="padding:18px 26px 24px 26px;">
                      <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                        font-size:12px;font-weight:600;color:#6b7c93;line-height:1.6;">
                        You’re receiving this email because you purchased a new course.
                        <br />
                        © {{year}} {{brand_name}}. All rights reserved.
                      </div>
                    </td>
                  </tr>
                </table>

              </td>
            </tr>

            <tr>
              <td style="padding:14px 8px 0 8px;">
                <div style="text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
                  font-size:11px;font-weight:600;color:#8a9bb3;line-height:1.6;">
                  {{brand_name}} • {{brand_address}}
                </div>
              </td>
            </tr>

          </table>
          <!-- /Container -->
        </td>
      </tr>
    </table>
  </body>
</html>
"""