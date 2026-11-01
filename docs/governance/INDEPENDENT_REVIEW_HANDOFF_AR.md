# نموذج مراجعة بشرية أمنية ووظيفية اختيارية — QA Lab

هذه قائمة تدقيق اختيارية. أُلغيت إلزامية المراجعة البشرية وفق قرار مالك المختبر بتاريخ 1 نوفمبر 2026؛ فلا تُشترط موافقة إنسانية قبل دمج PR أو لاكتمال فحوص CI. عند إجراء مراجعة طوعية لا يجوز الادعاء أنها تمت دون دليل من المراجع الحقيقي.

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

## عناصر تحقق عند إجراء المراجعة الاختيارية

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

## نموذج تقرير مراجعة اختيارية

يملؤه المراجع البشري إن اختار إجراء المراجعة؛ ولا يجوز لمساعد آلي انتحال توقيعه:

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
لا يُحتسب نجاح CI شهادة مراجعة بشرية، ولا يجعل غياب توقيع بشري CI فاشلًا. تبقى مراجعة الشيفرة اختيارية، وقرار النشر العام منفصلًا ولا يصدر من هذه الوثيقة.

## تفعيل الحماية والتحقق

راجع docs/governance/RULESET_ACTIVATION_AR.md. يجب التأكد من أن Ruleset الفعلي Active ويمنع الدمج دون فحوص GitHub Actions الأربعة الناجحة، مع صفر موافقات بشرية مطلوبة. يبقى إثبات منع الدمج التقني عبر PR تجريبي مستقلًا عن هذه المراجعة الاختيارية.
