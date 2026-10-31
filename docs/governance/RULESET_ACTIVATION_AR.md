# تفعيل حماية فرع main — المختبر المستقل

## الوضع الحالي

هذه الملفات **سياسة مقترحة قابلة للاستيراد** وليست حماية مفعّلة.
عند إعدادها، كانت إعدادات GitHub تعرض فرع main غير محمي وقائمة Rulesets فارغة،
ولا توجد مراجعة بشرية مستقلة مثبتة لإصدار واجهة المشغّل.
**لم تُفعّل** قواعد GitHub عبر تعديل هذا المستودع أو رفع الملف.

ملف الاستيراد: docs/governance/main-ruleset.json
والمتابعة: https://github.com/kafaat/kafaat-independent-qa-lab/issues/46

## قبل التفعيل

- يجب أن يكون في المستودع مراجع بشري مستقل ذو صلاحية مناسبة،
  غير مؤلف التغيير وغير صاحب آخر دفع. جميع طلبات السحب المفحوصة
  مؤخرًا كانت باسم kafaat؛ لا تفترض وجود مراجع مستقل بالفعل.
- إذا لم يوجد مراجع مؤهل، ادعُه وأكّد استعداده **قبل** تفعيل السياسة،
  لأن القاعدة ستمنع دمج أي Pull Request حتى يقدم الموافقة المطلوبة.
- لا تمنح المسؤول أو صاحب المستودع Bypass افتراضيًا دون قرار صريح
  موثق عن الاستثناءات والطوارئ. القالب لا يحدد قائمة Bypass.
- لا يجوز الاعتماد على نجاح CI كبديل عن موافقة بشرية، ولا تمنح
  السياسة أي تصريح للنشر الإنتاجي أو إغلاق الحالات.

## خطوات المستودع في GitHub

1. افتح https://github.com/kafaat/kafaat-independent-qa-lab/settings/rules
   بحساب يملك إدارة إعدادات المستودع.
2. اختر Settings → Rules → Rulesets → New ruleset → Import a ruleset.
3. استورد الملف main-ruleset.json من هذا المستودع، وراجع الاسم والنطاق
   target=branch وrefs/heads/main، ومستوى enforcement=active.
4. تحقق من وجود Require a pull request before merging وموافقة واحدة
   على الأقل، وDismiss stale approvals، وRequire approval of last push
   by someone else، وحل محادثات المراجعة قبل الدمج.
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

اختبار قبول الحماية المطلوب (بـPR اصطناعي منفصل لا يغير الإنتاج):
- افتح PR تجريبيًا بحالة CI ناجحة ودون موافقة؛ يجب أن يمنع الدمج.
- دع مراجعًا مستقلًا يوافق مع فحوص خضراء؛ يمكنه إثبات تحقق البوابة.
- بدّل ملف الاختبار أو اجعل فحصًا مطلوبًا فاشلًا ضمن PR تجريبي فقط؛
  يجب أن يمنع الدمج دون إعادة تشغيل آلي غير مفوض.
- أحدث تغيير بعد الموافقة يجب أن يجعل الموافقة القديمة غير كافية.
- أغلق/احذف فرع التجربة بعد توثيق النتيجة دون المساس بـmain.

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
