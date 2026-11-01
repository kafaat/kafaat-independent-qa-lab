# سياسة حماية main المعتمدة — المختبر المستقل

## القرار ونطاقه (1 نوفمبر 2026)

بقرار مالك المستودع، **المراجعة البشرية اختيارية** وليست شرطًا لدمج Pull Requests أو لاكتمال فحوص المختبر المحلي. أُلغي شرط الموافقة البشرية المستقلة الإلزامية والتدقيق البشري الرجعي الإلزامي. لا يجوز وصف مراجعة بشرية بأنها اكتملت دون أدلة حقيقية عليها.

**لا يعني موافقة على النشر الإنتاجي**: يبقى اعتماد النشر العام، والهويات متعددة المستخدمين، وRBAC والاستعادة والأمن التشغيلي قرارات منفصلة.

## آخر حالة حية مؤكدة — مطابقة للسياسة منذ 1 نوفمبر 2026، 20:00 بتوقيت عدن

- القاعدة: https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547
- عدّلها مالك المستودع من Settings؛ updated_at=2026-11-01T20:00:25.919+03:00.
- قراءة إدارية وتحقق بالأداة verify_live_ruleset.py عند 2026-11-01T17:00:39Z: **PASS**.
  - required_approving_review_count=0، وdismiss_stale_reviews_on_push وrequire_last_push_approval وrequired_review_thread_resolution وrequire_code_owner_review وrequire_extra_approval_for_unattributed_changes كلها false، وrequired_reviewers=[].
  - الفحوص الأربعة test وoperator-ui وroot-stack وhttp-stack من GitHub Actions App ID 15368، مع strict=true وdo_not_enforce_on_create=false.
  - deletion وnon_fast_forward مفعّلتان، وbypass_actors=[]، وcurrent_user_can_bypass=never، وmain.protected=true، وenforcement=active على refs/heads/main فقط.
- اسم القاعدة الحي ما زال "QA Lab main — independent review and verified CI"؛ لا أثر له في السلوك، ويمكن تغييره إلى "QA Lab main — required CI, optional human review".
- قبل التعديل كانت القاعدة تطلب موافقة واحدة مع البوابات الأربع الأخرى، ومنها require_extra_approval_for_unattributed_changes=true، وتظهر في الواجهة باسم "Require an additional approval for unattributed Copilot pull requests". بحسب وصف GitHub لا تضيف هذه البوابة موافقة إلا إذا كان العدد أكبر من صفر، لكن السياسة تعطّلها صراحةً حتى تبقى القاعدة الحية مطابقة للملف.
- القراءة المجهولة عبر API لا تعرض bypass_actors؛ لذلك لا يُقبل إثبات "Bypass فارغ" إلا من قراءة بحساب إداري.

### الاختبار السلوكي للسياسة الجديدة (1 نوفمبر 2026)

- PR تجريبي غير إنتاجي #52، أُغلق دون دمج:
  - التزام غير ضار 4769e94: 35/35 فحصًا ناجحًا، و0 مراجعات، وmergeable_state=**clean**. أثناء انتظار الفحوص كانت الحالة blocked.
  - التزام باختبار فاشل عمدًا 5b91b31: فشل فحص test الإلزامي وحده، ونجحت الفحوص الثلاثة الأخرى، وmergeable_state=**blocked**.
- PR #51 على aa502e1: 35/35 ناجحة، و0 مراجعات، وmergeable_state=clean بعد التعديل؛ وقبله على 707c7ee كانت الفحوص كلها ناجحة لكن الحالة blocked بسبب شرط الموافقة.
- الخلاصة: غياب الموافقة البشرية وحده لم يعد يمنع الدمج، وفشل فحص إلزامي ما زال يمنعه.

بعد أي تعديل لاحق في Settings يجب تشغيل أداة التحقق من جديد وتسجيل النتيجة. الخطوات أدناه مرجع لإعادة الضبط إن تغيّرت القاعدة.

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
5. ألغِ Dismiss stale pull request approvals وRequire approval of the most recent reviewable push وRequire conversation resolution before merging، وألغِ "Require an additional approval for unattributed Copilot pull requests"؛ لا تُلزم Code Owners أو فرق المراجعة.
6. اترك Require a pull request before merging مفعّلًا، واترك Required status checks والـstrict/up-to-date مفعّلين، مع الفحوص الأربعة ومصدر GitHub Actions.
7. اترك Restrict deletions وBlock force pushes مفعّلين، ولا تضف أي bypass actors.
8. اضغط Save changes وأكمل تأكيد الهوية إذا طلبه GitHub.
9. تحقّق بقراءة إدارية فقط: `python tools/governance/verify_live_ruleset.py`. تقرأ الأداة القاعدة 26292547 وفرع main بطلبات GET فقط، ويفشل إذا بقيت أي بوابة موافقة مفعّلة (بما فيها خيار Copilot)، أو غاب bypass_actors من الرد، أو ضعفت الفحوص الأربعة، أو لم يكن main محميًا. سجّل المخرجات ووقتها في القضية #46.

### بديل عبر GitHub CLI لتعديل القاعدة نفسها

بحساب مسؤول، يمكن تحديث القاعدة القائمة **نفسها** (PUT على الرقم 26292547، وليس إنشاء قاعدة ثانية) من ملف السياسة المعتمد مباشرة، ثم التحقق:

```sh
python tools/governance/activate_ruleset.py --policy main   # DRY RUN: يتحقق من الملف
gh api --method PUT repos/kafaat/kafaat-independent-qa-lab/rulesets/26292547 \
  --input docs/governance/main-ruleset.json
python tools/governance/verify_live_ruleset.py
```

ملف السياسة يضبط bypass_actors=[] صراحة، وكل بوابات المراجعة الخمس false صراحة، حتى لا يبقى أي خيار حي غير مذكور مفعّلًا.

**لا تستورد main-ruleset.json كقاعدة ثانية**: هذا المستودع فيه Ruleset حية برقم 26292547، والدمج في Git وحده لا يحدث أي تغيّر في Settings.

## مساعد GitHub CLI — DRY RUN أولًا

من نسخة المستودع، بعد تسجيل مسؤول مخوّل:

```sh
gh auth login --hostname github.com
python tools/governance/activate_ruleset.py --policy main
```

الخيار ci-only مرادف لـ main. أزيلت خيارات independent-review و--reviewer-ready؛ لا تُستخدم. لا تنفذ --apply لإنشاء قاعدة ثانية موجودة أصلًا. المساعد ينشئ فقط ولا يحدّث القواعد الحية، ويتحقق من تكرار الاسم عبر جميع صفحات API. التحقق من القاعدة الحية بعد التعديل يتم بالأداة المنفصلة verify_live_ruleset.py، وهي للقراءة فقط.

## اختبار قبول قواعد الحماية

على PR اصطناعي منفصل وغير إنتاجي، اختبر منع الدمج عند فشل أحد الفحوص الأربعة أو غيابه، مع تأكيد النجاح عند استيفائها دون مطالبة بموافقة بشرية. وثّق نتيجة GitHub الفعلية؛ مجرد اختبار وحدة للقالب لا يثبت فرض القاعدة. لا تدمج PR التجريبي ولا تشغّل workflows يدويًا دون تفويض منفصل.

## حدود القرار

CI المتحقق وإيصالات SHA256 دليل تنفيذ فقط، وليسا مراجعة بشرية أو قبولًا إنتاجيًا. تشغيل الواجهة محلي على 127.0.0.1؛ لا تعريض عام ولا نشر متعدد المستخدمين قبل إضافة الضوابط الخاصة بذلك والتفويض المنفصل. لا يملك المختبر سلطة إغلاق حالات المستشفى أو نشر أنظمة الإنتاج.
