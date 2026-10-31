# تسليم المراجعة الأمنية والوظيفية المستقلة — QA Lab

هذه وثيقة لتيسير مراجعة **بشرية مستقلة**، وليست موافقة صادرة
من مساعد آلي أو من منفّذ التغيير.

## نطاق المراجعة الثابت

المستودع: kafaat/kafaat-independent-qa-lab
- PR #44 (مدمج): https://github.com/kafaat/kafaat-independent-qa-lab/pull/44
- التزام الدمج: fece57452e602a835845bf7e860aa0235b8bc45a
- SHA للشجرة المدمجة: 31f230f88b16d8971035f83bbdaba46a0c7b9820
- وثيقة الحدود: docs/OPERATOR_SECURITY_REVIEW.md
- إيصال القبول داخل Docker:
  https://github.com/kafaat/kafaat-independent-qa-lab/actions/runs/41235591550/artifacts/13055606206
- قبول المكدس الجذري:
  https://github.com/kafaat/kafaat-independent-qa-lab/actions/runs/41235591545

## الأدلة الآلية المتحققة سابقًا

- 11 تشغيل GitHub Actions على التزام الدمج، كلها ناجحة (20 وظيفة و155 خطوة).
- Python regression: 585 اختبارًا، منها 30 skipped لا تعني نجاح اختبارها.
- Operator Compose: PASS 40/40، تم إنشاء/تنظيف الموارد المؤقتة،
  والمصدر والشجرة دون تغيير، production_accepted=false.
- Root Compose: PASS 26/26؛ يحتفظ بإثبات عدم الكتابة عند القراءة.
- بوابات الأمن والوظائف شغلت login/logout وCSRF وHost وSQL injection
  والتحويلات وصدق الإيصال والتحقق من SHA والوصفة المسموحة.

## عناصر تحقق يوقعها المراجع البشري

1. فصل ثقة UI عن API: لا يظهر Bearer في HTML أو Cookie أو URL؛
   الاتصالات الداخلية لا تتبع redirects أو proxy خارجي، وواجهة
   التشغيل لا تحمل mount قاعدة البيانات أو workspace.
2. التحقق من أن Host كامل authority محلي فقط، وعدم قبول userinfo
   أو DNS rebinding؛ لا توجد منافذ Docker غير loopback.
3. Cookie عشوائي HttpOnly وSameSite=Strict، CSRF لكل جلسة،
   سياسة Origin صارمة، جلسة منتهية، وإبطال عند logout/restart.
4. XSS escaping للمدخلات والسجلات، وCSP بدون نصوص JavaScript
   مضمّنة؛ حدود طلب HTTP ومنع SQL injection.
5. المسار الوظيفي: الدخول → إرسال python-smoke عبر API →
   worker على checkout SHA موثوق → إيصال SHA256 صحيح →
   تفصيل التشغيل والفلترة → إعادة مفتاح idempotency دون تكرار.
6. رفض الوصفات العامة والأوامر الحرة، وعدم منح المختبر سلطة
   إغلاق حالات المستشفى أو قبول الإنتاج.
7. التأكد من أن 30 skip في مجموعة Python تخص اختبارات مخصصة
   وأن نتائجها لا تُعتمد بلا بيئاتها المخصصة.
8. تأكيد عدم تسرب أسرار أو بيانات شخصية إلى artifacts أو
   Docker stdout، وفحص مصدر SHA/tree والتخلص من الموارد.
9. توثيق المخاطر المتبقية: لا حسابات مستخدمين متعددة ولا RBAC
   ولا SSO ولا قبول HTTPS عام ولا اختبار استعادة إنتاجية.

## نموذج قرار المراجع

يملأه المراجع البشري المخوّل في قضية GitHub #46، وليس المساعد:

- GitHub reviewer username: _____________________
- Affiliation / independence from implementing author: _____________________
- Reviewed commit and tree SHA: _____________________
- Checked evidence artifact ID and hash: _____________________
- Security verdict: APPROVED / CHANGES_REQUESTED / BLOCKED
- Functional verdict: APPROVED / CHANGES_REQUESTED / BLOCKED
- Findings, severity, mitigations and residual risks: _____________________
- Review date/time: _____________________
- Review link / signature: _____________________

**لا تملأ الحقول بقيم افتراضية أو توقيع اصطناعي.**
لا يعتبر نجاح CI أو تعليق في issue موافقة مستقلة.
ويظل قرار النشر منفصلًا حتى بعد موافقة هذا المراجع.

## تفعيل الحماية والتحقق

راجع docs/governance/RULESET_ACTIVATION_AR.md. يجب التأكد من
أن Ruleset الفعلي Active ويمنع دمج PR دون فحوص ناجحة وموافقة
بشرية مستقلة، ثم توثيق نتائج الاختبار الإيجابي والسلبي في #46.
