# سياسة حماية main المعتمدة — المختبر المستقل

## القرار ونطاقه (1 نوفمبر 2026)

بقرار مالك المستودع، **المراجعة البشرية اختيارية** وليست شرطًا لدمج Pull Requests أو لاكتمال فحوص المختبر المحلي. أُلغي شرط الموافقة البشرية المستقلة الإلزامية والتدقيق البشري الرجعي الإلزامي. لا يجوز وصف مراجعة بشرية بأنها اكتملت دون أدلة حقيقية عليها.

**لا يعني موافقة على النشر الإنتاجي**: يبقى اعتماد النشر العام، والهويات متعددة المستخدمين، وRBAC والاستعادة والأمن التشغيلي قرارات منفصلة.

## آخر حالة حية مؤكدة — تختلف عن السياسة المطلوبة

- القاعدة الحالية: https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547
- آخر قراءة في 1 نوفمبر 2026: enforcement=active، scope=refs/heads/main، Bypass فارغ.
- **تطلب حاليًا موافقة واحدة**: required_approving_review_count=1، مع last-push approval وdismiss_stale_reviews_on_push وrequired_review_thread_resolution مفعّلة.
- جرى هذا التغيير بالتزامن مع تحضير سياسة الإلغاء. لذا **لم يُنفّذ قرار المالك في إعدادات GitHub الفعلية بعد**.
- الفحوص الأربعة الإلزامية لم تتغير: test وoperator-ui وroot-stack وhttp-stack (GitHub Actions App ID 15368)، مع strict checks ومنع الحذف وforce push.

القالب الجديد هو **سياسة مستهدفة بعد التفعيل**، ولا تعدل ملفات JSON قواعد GitHub تلقائيًا. بعد إزالة شرط الموافقة من Settings يجب تأكيد الصفر بقراءة API جديدة.

## الملفات الرسمية

- docs/governance/main-ruleset.json: السياسة المعتمدة دون موافقة بشرية إلزامية.
- docs/governance/main-ruleset-ci-only.json: اسم قديم متوافق مع القاعدة الحية وقيمه مطابقة للملف الرسمي.
- docs/governance/INDEPENDENT_REVIEW_HANDOFF_AR.md: قائمة تدقيق اختيارية.
- docs/governance/DECISION_20261101_NO_HUMAN_GATE_AR.md: سجل قرار النطاق والمخاطر.

## تعديل القاعدة الموجودة في Settings — الخطوة التنفيذية اللازمة

1. افتح https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547 بحساب لديه صلاحيات الإدارة.
2. اختر Edit. يمكن تغيير اسم القاعدة إلى "QA Lab main — required CI, optional human review".
3. افتح Show additional settings أسفل Require a pull request before merging.
4. اضبط Required approvals على **0**.
5. ألغِ Dismiss stale pull request approvals وRequire approval of the most recent reviewable push وRequire conversation resolution before merging؛ لا تُلزم Code Owners أو فرق المراجعة.
6. اترك Require a pull request before merging مفعّلًا، واترك Required status checks والـstrict/up-to-date مفعّلين، مع الفحوص الأربعة ومصدر GitHub Actions.
7. اترك Restrict deletions وBlock force pushes مفعّلين، ولا تضف أي bypass actors.
8. اضغط Save changes وأكمل تأكيد الهوية إذا طلبه GitHub.
9. افتح https://api.github.com/repos/kafaat/kafaat-independent-qa-lab/rulesets/26292547 وتأكد من required_approving_review_count=0 ومن بقية القيم، ثم تأكد أن main.protected=true.

**لا تستورد main-ruleset.json كقاعدة ثانية**: هذه المستودع فيه Ruleset حية برقم 26292547، والدمج في Git وحده لا يحدث أي تغيّر في Settings.

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
