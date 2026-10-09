# email_template.py (UIFPE-444)
# The Beacon export email, adapted from the SFS FileShare template (Pearson
# brand: Plus Jakarta Sans, purple palette, dark footer). It lives as a Python
# string because the Lambda zip ships *.py only (build-lambda-zip.ps1) — an
# .html file would silently not deploy.
#
# Tokens the adapter fills: ${exportName}, ${batchId}, ${filesLists},
# ${downloadLink}. No presigned links ride in the mail (owner decision,
# 2026-09-16): the Download button opens the export's page in the Beacon
# panel, which mints fresh 15-minute links when the recipient clicks Files.

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Export Ready</title>
</head>
<body style="margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #EDECF6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #EDECF6;">
    <tr>
        <td align="center" style="padding: 40px 20px;">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                   style="background-color: #FFFFFF; border-radius: 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); max-width: 600px;">

                <!-- Top Bar: Light Purple -->
                <tr>
                    <td style="background-color: #C1BFFF; padding: 6px 0; border-radius: 20px 20px 0 0;"></td>
                </tr>

                <!-- Pearson Logo -->
                <tr>
                    <td align="center" style="padding: 30px 40px 10px 40px;">
                        <a href="https://www.pearson.com/en-gb.html">
                            <img src="https://ukschpgs.pearson.com/l/749453/2025-04-09/57jszq/749453/1744191320vnZjKPDO/PEARSON_LOGO_PURPLE_RGB.png"
                                 alt="Pearson"
                                 style="max-width: 200px; outline: none; text-decoration: none; width: 200px;"
                                 width="200"/>
                        </a>
                    </td>
                </tr>

                <!-- Headline Section -->
                <tr>
                    <td style="padding: 10px 40px 20px 40px;">
                        <p style="margin: 0; font-size: 16px; line-height: 21px; color: #0D004D; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                            Your Beacon export has new files. Access them using the details below.
                        </p>
                    </td>
                </tr>

                <!-- Info Panel: Light Purple Background -->
                <tr>
                    <td style="padding: 0 40px 30px 40px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                               style="background-color: #C1BFFF; border-radius: 20px;">
                            <tr>
                                <td style="padding: 20px;">
                                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">

                                        <tr>
                                            <td style="padding: 12px 0; border-bottom: 1px solid #FFFFFF;">
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                                    <tr>
                                                        <td width="130"
                                                            style="font-size: 16px; font-weight: 600; color: #0d004d; vertical-align: top; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                                                            Export
                                                        </td>
                                                        <td style="font-size: 16px; color: #0D004D; font-weight: 400; font-family: 'Plus Jakarta Sans', arial, sans-serif; line-height: 21px;">
                                                                ${exportName}
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>

                                        <tr>
                                            <td style="padding: 12px 0; border-bottom: 1px solid #FFFFFF;">
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                                    <tr>
                                                        <td width="130"
                                                            style="font-size: 16px; font-weight: 600; color: #0d004d; vertical-align: top; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                                                            Batch
                                                        </td>
                                                        <td style="font-size: 16px; color: #0D004D; font-weight: 400; font-family: 'Plus Jakarta Sans', arial, sans-serif; line-height: 21px;">
                                                                ${batchId}
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>

                                        <tr>
                                            <td style="padding: 12px 0 0 0;">
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                                    <tr>
                                                        <td width="130"
                                                            style="font-size: 16px; font-weight: 600; color: #0d004d; vertical-align: top; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                                                            Files
                                                        </td>
                                                        <td style="font-size: 16px; color: #0D004D; line-height: 21px; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                                                                ${filesLists}
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>

                                    </table>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>

                <!-- CTA Button: Dark Purple -->
                <tr>
                    <td align="center" style="padding: 0 40px 40px 40px;">
                        <a href="${downloadLink}"
                           style="display: inline-block; background-color: #0D004D; color: #ffffff; text-decoration: none; padding: 10px 32px; border-radius: 30px; font-size: 16px; font-weight: 600; text-align: center; font-family: 'Plus Jakarta Sans', arial, sans-serif; border: 2px solid #0D004D; line-height: 30px;">
                            Download
                        </a>
                    </td>
                </tr>

            </table>

            <!-- Dark Footer Section -->
            <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                   style="background-color: #0d004d; max-width: 600px; margin-top: 0; border-radius: 0 0 20px 20px;">

                <!-- Copyright -->
                <tr>
                    <td align="center"
                        style="padding: 20px 20px 10px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 15px; text-align: center;">
                        Copyright © 2026
                    </td>
                </tr>

                <!-- Company Info -->
                <tr>
                    <td align="center"
                        style="padding: 10px 20px 20px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 15px; text-align: center;">
                        Pearson Education Ltd&nbsp;|&nbsp;Registered No: 872828&nbsp;|&nbsp;80 Strand London WC2R 0RL
                    </td>
                </tr>

                <!-- Links -->
                <tr>
                    <td align="center"
                        style="padding: 0 20px 20px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 115%; text-align: center;">
                        <a href="https://www.pearson.com/privacy-center/privacy-notices.html"
                           style="color: #ffffff; text-decoration: none;">Privacy notice</a>
                        <span style="color: #ffffff;">&nbsp;|&nbsp;</span>
                        <a href="https://www.pearson.com/en-gb/legal-information/cookie-policy.html"
                           style="color: #ffffff; text-decoration: none;">Cookies</a>
                    </td>
                </tr>

            </table>
        </td>
    </tr>
</table>
</body>
</html>"""
