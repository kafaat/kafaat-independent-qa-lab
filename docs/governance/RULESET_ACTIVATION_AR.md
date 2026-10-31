# تفعيل حماية فرع main — المختبر المستقل

## الوضع الحالي

هذه الملفات **سياسة مقترحة قابلة للاستيراد** وليست حماية مفعّلة.
عند إعدادها، كانت إعدادات GitHub تعرض فرع main غير محمي وقائمة Rulesets فارغة،
ولا توجد مراجعة بشرية مستقلة مثبتة لإصدار واجهة المشغّل.
**لم تُفعّل** قواعد GitHub عبر تعديل هذا المستودع أو رفع الملف.

ملف الاستيراد: docs/governance/main-ruleset.json
والمتابعة: https://github.com/kafaat/kafaat-independent-qa-lab/issues/46

يوجد أيضًا اقتراح مؤقت لفحوص CI فقط، بلا بوابة موافقة بشرية:
docs/governance/main-ruleset-ci-only.json. لا يُكمل هذا الاقتراح
متطلبات المراجعة المستقلة.

## قبل التفعيل

- سياسة `ci-only` مرحلة مؤقتة لحماية فحوص CI وطلب السحب فقط؛ **لا تتطلب موافقة بشرية** ولا تثبت إنجاز المراجعة المستقلة. يمكن تطبيقها من مسؤول مخوّل دون انتظار المراجع، بعد التأكد من عمل الفحوص الأربعة على PR.
- سياسة `independent-review` هي بوابة المراجعة البشرية الكاملة؛ **لا تفعّلها قبل تأكيد توفر مراجع بشري مؤهل** غير صاحب التغيير وغير صاحب آخر دفع، وإلا قد يتعذر دمج الطلبات اللاحقة.
- لا تمنح المسؤول أو صاحب المستودع Bypass افتراضيًا دون قرار موثق؛ القالبان لا يحددان Bypass.
- يظل اعتماد النشر الإنتاجي وإغلاق قضية #46 قرارين منفصلين حتى بعد تفعيل إحدى القاعدتين.

## خطوات المستودع في GitHub

يمكن لمسؤول مخوّل استخدام مساعد Python المعتمد على المكتبة القياسية بدل الاستيراد اليدوي.
يبدأ المساعد دائمًا بوضع DRY RUN، ولا يرسل طلبًا إلى GitHub إلا مع
`--apply`. يتطلب CLI المصادَق عليه عبر `gh auth login --hostname github.com` وصلاحية إدارة
المستودع. جميع طلبات API مربوطة صراحةً بالمضيف `github.com` حتى إذا ضُبط
`GH_HOST` لمضيف مؤسسة آخر. لا تضع رمزًا أو كلمة مرور في وسيطات الأمر أو الملفات.

```sh
python tools/governance/activate_ruleset.py --policy ci-only
python tools/governance/activate_ruleset.py --policy ci-only --apply
```

لا تستخدم سياسة المراجعة الصارمة إلا بعد تأكيد توافر مراجع مستقل مؤهل؛
المساعد يرفض تطبيقها دون إقرار صريح:

```sh
python tools/governance/activate_ruleset.py --policy independent-review
python tools/governance/activate_ruleset.py --policy independent-review --reviewer-ready --apply
```

المساعد ينشئ قاعدة ولا يثبت وحده أن إعدادات GitHub الفعلية تطابقها.
افحص الحالة والـeffective branch rules يدويًا بعد الإنشاء. إذا ظهرت قاعدة
بالاسم نفسه، يتوقف المساعد بدل إنشاء نسخة مكررة؛ افحص القاعدة الحالية
وتعامل معها من إعدادات GitHub.

1. افتح https://github.com/kafaat/kafaat-independent-qa-lab/settings/rules
   بحساب يملك إدارة إعدادات المستودع.
2. اختر Settings → Rules → Rulesets → New ruleset → Import a ruleset.
3. استورد الملف الذي اخترته: `main-ruleset-ci-only.json` لحماية CI مؤقتًا،
   أو `main-ruleset.json` لبوابة المراجعة المستقلة. راجع الاسم والنطاق
   `target=branch` و`refs/heads/main` ومستوى `enforcement=active`.
4. تأكد من Require a pull request before merging للسياستين. **فقط عند
   `independent-review`** تحقق من موافقة مستقلة واحدة على الأقل، وDismiss
   stale approvals وRequire approval of last push by someone else وحل
   محادثات المراجعة قبل الدمج؛ `ci-only` لا تفرض هذه الموافقات.
5. تحقق من Required status checks (مصدر GitHub Actions، App ID 15368):
   test وoperator-ui وroot-stack وhttp-stack.
   يجب أن تعمل هذه الـjobs الأربعة دون شروط path أو if تمنع تشغيلها
   على pull_request، وأن تجتاز PR في أحدث نسخة من main.
   لا تضع وظائف PR-only شرطًا مطلوبًا على أحداث push غير المدعومة.
6. تأكد أن فرع main لا يسمح بالحذف أو force push، وأن Strict/Require
   branches to be up to date before merging مفعّل. افحص Bypass list
   ولا تترك استثناءات غير موثقة.
7. احفظ القاعدة. **الاستيراد وحده ليس إثباتًا للتفعيل**؛ افتح قائمة
   Rulesets مجددًا وتحقق من Active ومن Effective branch rules.

وثائق GitHub الرسمية:
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository

## التحقق بعد التفعيل

من واجهة GitHub، تحقق من الحالة الفعلية للقاعدة ومن قائمة
Required checks. في واجهة REST للقراءة استخدم:
- GET /repos/kafaat/kafaat-independent-qa-lab/rulesets?includes_parents=true
- GET /repos/kafaat/kafaat-independent-qa-lab/rules/branches/main

سجّل في issue #46: Rule ID ورابطها، اسم من فعّلها وتاريخ التفعيل،
والـrequired checks المحققة، وعدم وجود bypass غير مصرح به.
لا تكتفِ بحقل protected في استعلام branches؛ افحص الـrulesets
وقواعد الفرع الفعلية أيضًا.

## اختبار قبول سياسة ci-only (مؤقتة)

نفّذ التجارب على PR اختبار منفصل دون دمج أو تغيير الإنتاج:
- تحقّق من أن الفحوص المطلوبة `test` و`operator-ui` و`root-stack` و`http-stack` ظاهرة وناجحة، وأن غياب موافقة بشرية **ليس سببًا للمنع بواسطة هذه السياسة**؛ فهي لا تتطلب موافقة بشرية.
- اجعل أحد الفحوص الإلزامية فاشلًا في PR اختباري؛ يجب أن يمنع GitHub الدمج.
- تحقّق من فرض PR ومن منع الحذف وforce push على `main`.
- نجاح هذه التجارب **لا يحقق المراجعة البشرية المستقلة** المطلوبة بالقضية #46.

## اختبار قبول سياسة independent-review

لا تنفّذ هذه المرحلة إلا بعد تفعيل سياسة المراجعة بوجود مراجع مستقل:
- افتح PR تجريبيًا بحالة CI ناجحة ودون موافقة؛ يجب منع الدمج.
- اطلب من مراجع مستقل مؤهل الموافقة مع جميع الفحوص الخضراء؛ تأكد من تحقق بوابة الموافقة، دون دمج تجريبي غير مفوّض.
- اجعل أحد الفحوص المطلوبة فاشلًا؛ يجب منع الدمج حتى مع وجود الموافقة.
- أحدث تغيير بعد الموافقة يجب أن يبطل كفاية الموافقة القديمة.
- اختبر بقاء خيط مراجعة مفتوح ومنع الدمج حتى يُحل.
- أغلق/احذف فرع التجربة بعد توثيق أدلة المنع والسماح دون المساس بـ`main`.
- بعد تفعيل السياسة الكاملة، قيّم تعطيل القاعدة المؤقتة `ci-only` لتجنب تداخل قواعد الحماية، بعد التحقق من القواعد الفعلية.

## فصل القرار

نجاح PR #44 (20 workflows، 36 jobs، 302 steps) وإيصال Docker 40/40
على SHA 4c7925e64ffa123c1d1add12c898de07508e0617، ونجاح فحوص
merge SHA fece57452e602a835845bf7e860aa0235b8bc45a،
كل ذلك إثبات CI لا شهادة مراجعة بشرية مستقلة أو قبول نشر عام.

حتى تصبح البوابة مكتملة نحتاج:
(1) قواعد Active محققة، (2) موافقة بشرية مسجلة من مراجع مستقل،
(3) تجربة سلبيات/إيجابيات الـRuleset، (4) قرار صريح منفصل لأي نشر.

## قيود أدوات التشغيل

GitHub connector المتاح لهذه الجلسة يتيح قراءة قواعد حماية الفروع
وإدارة الشيفرة وPRs، لكنه لا يوفر تعديل Rulesets/branch protection
عبر إجراء معتمد. لذلك يظل الاستيراد والموافقة البشرية خطوات لم تتم،
ولا يجوز تسجيلها PASS اعتمادًا على وجود هذا الملف.
