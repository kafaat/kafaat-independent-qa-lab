# سياسة حماية main المعتمدة — المختبر المستقل

## القرار ونطاقه (1 نوفمبر 2026)

بقرار مالك المستودع، **المراجعة البشرية اختيارية** وليست شرطًا لدمج Pull Requests أو لاكتمال فحوص المختبر المحلي. أُلغي شرط الموافقة البشرية المستقلة الإلزامية والتدقيق البشري الرجعي الإلزامي. لا يجوز وصف مراجعة بشرية بأنها اكتملت دون أدلة حقيقية عليها.

**لا يعني موافقة على النشر الإنتاجي**: يبقى اعتماد النشر العام، والهويات متعددة المستخدمين، وRBAC والاستعادة والأمن التشغيلي قرارات منفصلة.

## الحالة الحية المثبتة من GitHub API

- Rule: https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547
- Rule ID: 26292547؛ enforcement=active؛ scope=refs/heads/main؛ Bypass list فارغة.
- الاسم التاريخي: QA Lab main — CI only (temporary). كلمة temporary في الاسم لا تمثل انتظار بوابة بشرية مستقبلية.
- يفرض Pull Request والفحوص الأربعة من GitHub Actions: test وoperator-ui وroot-stack وhttp-stack (App ID 15368).
- strict_required_status_checks_policy=true، ومنع deletion وnon_fast_forward.
- required_approving_review_count=0، ولا يلزم dismiss stale approvals ولا last-push approval ولا conversation resolution.

هذه حالة مثبتة بتاريخ القرار؛ إعادة فحص API واجبة بعد أي تغيير. لا يعتمد قبول الحماية على وجود ملف JSON وحده.

## الملفات الرسمية

- docs/governance/main-ruleset.json: السياسة المعتمدة دون موافقة بشرية إلزامية.
- docs/governance/main-ruleset-ci-only.json: اسم قديم متوافق مع القاعدة الحية وقيمه مطابقة للملف الرسمي.
- docs/governance/INDEPENDENT_REVIEW_HANDOFF_AR.md: قائمة تدقيق اختيارية.
- docs/governance/DECISION_20261101_NO_HUMAN_GATE_AR.md: سجل قرار النطاق والمخاطر.

## تفعيل أو مراجعة الإعدادات في Settings

1. افتح https://github.com/kafaat/kafaat-independent-qa-lab/settings/rules
2. افتح القاعدة القائمة #26292547 وتأكد أنها Active وتستهدف main.
3. راجع وجود Pull Request والفحوص الأربعة ومصدر GitHub Actions، ومنع الحذف وforce push وعدم وجود Bypass.
4. لا تُنشئ قاعدة ثانية: القاعدة الحالية مفعّلة. تغيير الاسم التاريخي - عند الحاجة - يتم بتعديل القاعدة القائمة فقط.
5. اقرأ https://api.github.com/repos/kafaat/kafaat-independent-qa-lab/rulesets?includes_parents=true
6. اقرأ https://api.github.com/repos/kafaat/kafaat-independent-qa-lab/rulesets/26292547

الشرط البشري أُلغي، لكن صلاحية المسؤول اللازمة لتعديل Settings تبقى ضرورة أمنية منفصلة عن مراجعة الشيفرة.

## مساعد GitHub CLI — DRY RUN أولًا

من نسخة المستودع، بعد تسجيل مسؤول مخوّل:

```sh
gh auth login --hostname github.com
python tools/governance/activate_ruleset.py --policy main
```

الخيار ci-only مرادف لـ main. أزيلت خيارات independent-review و--reviewer-ready؛ لا تُستخدم. لا تنفذ --apply لإنشاء قاعدة ثانية موجودة أصلًا. المساعد ينشئ فقط ولا يحدّث القواعد الحية، ويتحقق من تكرار الاسم عبر جميع صفحات API.

## اختبار قبول قواعد الحماية

على PR اصطناعي منفصل وغير إنتاجي، اختبر منع الدمج عند فشل أحد الفحوص الأربعة أو غيابه، مع تأكيد النجاح عند استيفائها دون مطالبة بموافقة بشرية. وثّق نتيجة GitHub الفعلية؛ مجرد اختبار وحدة للقالب لا يثبت فرض القاعدة. لا تدمج PR التجريبي ولا تشغّل workflows يدويًا دون تفويض منفصل.

## حدود القرار

CI المتحقق وإيصالات SHA256 دليل تنفيذ فقط، وليسا مراجعة بشرية أو قبولًا إنتاجيًا. تشغيل الواجهة محلي على 127.0.0.1؛ لا تعريض عام ولا نشر متعدد المستخدمين قبل إضافة الضوابط الخاصة بذلك والتفويض المنفصل. لا يملك المختبر سلطة إغلاق حالات المستشفى أو نشر أنظمة الإنتاج.
