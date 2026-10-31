# مراجعة أمنية ووظيفية لواجهة المشغل المحلية (PR #44)

## الأصول وحدود الثقة

الرموز الحساسة: QA_API_TOKEN وكلمة مرور المشغل وتجزئة PBKDF2،
وجلسات المتصفح وCSRF. الشيفرة وبيانات SQLite والإيصالات تعتبر
أصولًا ذات صلاحيات منفصلة. UI لا تملك mount للبيانات ولا workspace،
والعامل وحده يشغل الوصفات المسموحة، ولا يملك المختبر سلطة نشر أو إغلاق.

## مراجعة المسارات

| التهديد | التدبير القائم | دليل التحقق |
| --- | --- | --- |
| Bearer في المتصفح | صفحات HTML خادمية، والرمز داخل خدمة UI فقط | اختبارات Console وCompose |
| Redirect أو Proxy يسرّب Bearer | منع إعادة التوجيه والـProxy؛ حصر عنوان API الداخلي | RedirectSafetyTests وHostAndBackendBoundaryTests |
| DNS rebinding أو Host userinfo | التحقق من authority المحلي الكامل لا hostname وحده | test_complete_host_authority_cannot_include_userinfo |
| CSRF | سر لكل جلسة، واختبار Origin | اختبارات HTTP وCompose |
| XSS | HTML escaping وCSP بلا script | test_html_escaping_detail_and_pagination |
| SQL injection | استعلامات parameterized وحدود البحث/التقسيم | test_run_catalog |
| تنفيذ أوامر حرة | allowlist وworker مستقل وSHA للنسخة | test_console_e2e وCompose |
| تزوير الإيصال | بصمة SHA256 وفصل إثبات التنفيذ عن قبول الإصدار | اختبارات tampering وCompose |
| كتابة UI لقاعدة البيانات | UID=10001 ونظام ملفات read-only ودون mount | تحقق حاوية operator-ui |
| انتحال الجلسة | HttpOnly وSameSite وlogout ومدة محددة | اختبارات الجلسات وCompose |

لا يخزن إيصال القبول كلمة مرور أو cookie أو Bearer أو نصوص
إيصالات التنفيذ؛ يخزن فقط بيانات المشروع المؤقت والـSHA والبصمة
والنتائج وحالة تنظيف الموارد.

## المخاطر التي تظل قائمة بعد نجاح CI

هذه واجهة لمشغل واحد على localhost فقط، ولا يوجد نظام
مستخدمين متعددين أو RBAC على مستوى المشاريع أو SSO.
لا يسمح بنشرها على الإنترنت أو الاعتماد الإنتاجي قبل تطوير
تلك الطبقات ومراجعة HTTPS والتدقيق والنسخ الاحتياطي.

تحتاج موافقة المراجع الأمني والوظيفي **المستقل** إلى توقيع صريح
داخل PR، ولا تستبدلها مراجعة الشيفرة الذاتية أو نجاح الاختبارات.
لا تعطي هذه المراجعة تفويضًا للدمج أو إعادة تشغيل workflows
يدويًا أو نشر Railway أو اعتماد إصدار.
