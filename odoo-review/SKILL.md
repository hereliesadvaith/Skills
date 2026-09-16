---
name: odoo-review
description: 'Expert in Odoo access control: ir.model.access.csv, record rules (ir.rule),
  groups, and multi-company security patterns. Writes and reviews security files for Odoo 19.'
risk: safe
source: self
license: MIT
---
# Odoo Access Control Review

## Overview

Security in Odoo is managed at three levels:

1. **Model-level access** (`ir.model.access.csv`): which groups can read / write / create / unlink a model at all.
2. **Record-level rules** (`ir.rule`): which records a user can see or touch, via a domain.
3. **Field-level access** (`groups=` on a field definition): which groups can read / write a specific field.

This skill writes correct security files for a custom module and reviews existing ones for the mistakes that cause "Access Denied" errors or, worse, silently over-grant.

**Target version: Odoo 19.** Everything below is checked against the Odoo 19 source. Where 18/19 changed behaviour relative to 17 and earlier, it is called out with a *Version note*.

## When to Use This Skill

- Setting up access rights for a new custom module.
- Reviewing a module's `security/` folder before release.
- Restricting records so users only see their own data or their company's data.
- Debugging "Access Denied" / "You are not allowed to access" / "Due to security restrictions" errors.
- Implementing multi-company or portal record visibility.

## How It Works

1. **Activate**: Mention `@odoo-review` and describe the access scenario, or point at a module's `security/` folder.
2. **Generate**: Get correct CSV access lines, group / privilege XML, and record rules.
3. **Review**: Get the checklist below applied to the module, with concrete fixes.
4. **Debug**: Paste an access error and get a diagnosis following the debugging workflow.

## Review Checklist

Run through this for every model the module defines:

- [ ] There is at least one `ir.model.access.csv` line per model (including transient / wizard models and `_inherits` children).
- [ ] Every ACL line has a `group_id`. A blank group grants access to **every** user, including portal and public users. Odoo 19 logs `this is a deprecated feature` for such lines.
- [ ] Regular users do not have `perm_unlink = 1` unless deletion is part of the business process.
- [ ] Manager-level ACLs use a module-specific group, not `base.group_system` or `base.group_erp_manager`.
- [ ] Each custom group has a `privilege_id` so it appears under a heading on the user form, and `implied_ids` chains to `base.group_user` (or to the module's user group).
- [ ] Every `ir.rule` that is meant to restrict a group has `<field name="groups">`. A rule without groups is **global** and applies to everyone, including admins.
- [ ] If an "own records" rule exists for the user group, a widening rule exists for the manager group (see how rules combine below).
- [ ] Models with a `company_id` field have a multi-company rule using `company_ids`.
- [ ] Portal-facing models have a `base.group_portal` rule that pins to `user.partner_id` (or its commercial partner), and their ACL for portal is read-only unless editing is a feature.
- [ ] Sensitive fields (salary, cost, internal notes) carry `groups=` on the field definition.
- [ ] No `sudo()` in controllers or compute methods that returns data the user could not otherwise read.
- [ ] The manifest lists `security/*_groups.xml` **before** `security/ir.model.access.csv` and `security/*_rules.xml`, so group xmlids resolve.

## How Record Rules Combine

This is the single most common source of "why can this user still see that" confusion.

- **Global rules** (no groups) are **AND**-ed together. Every global rule must pass.
- **Group rules** (with groups) for all groups the user belongs to are **OR**-ed together. Any one passing is enough.
- The final domain is `AND(all global rules, OR(all matching group rules))`.
- If the user matches **no** group rule and there is no global rule, access is unrestricted at record level (ACL still applies).

Consequence: an "own records only" rule for `group_user` alone will also restrict managers, because managers are (through `implied_ids`) members of `group_user`. You must add a second rule for the manager group with a permissive domain like `[(1, '=', 1)]`; it is OR-ed in and widens access.

## Verifying Against a Live Instance

You can use the **`odoo-acl`** skill to check access control on a running Odoo instance over XML-RPC instead of guessing from the code. Useful calls (Odoo 19 method names):

- `ir.model.access` `search_read` with `[["model_id.model", "=", "hospital.patient"]]` to list the effective ACL lines for a model.
- `ir.rule` `search_read` with the same domain, fields `name, domain_force, groups, global, perm_read, perm_write, perm_create, perm_unlink`. `global = true` means the rule has no group.
- `<model>` `has_access` with `[[], "write"]` to test model-level access for the connected user, or `[[id, ...], "write"]` to test specific records. `check_access` does the same but raises.
- `res.users` `has_group` with `[[uid], "base.group_user"]` to confirm which groups the connected user actually belongs to. In 19 you may only call it for your own user unless you are an internal user.

*Version note:* `check_access_rights` and `check_access_rule` still work but are deprecated since Odoo 18.

Run these as the affected (non-admin) user whenever possible. The admin user is in `base.group_system`, which implies almost everything, so it proves nothing about a restricted role.

## Examples

All examples use a `hospital` module with a `hospital.patient` model that has a `company_id` field.

### Example 1: Privilege and Groups (`security/hospital_groups.xml`)

*Version note:* Odoo 19 replaced `res.groups.category_id` with `privilege_id` pointing at a `res.groups.privilege`. The privilege holds the `category_id`. Groups without a privilege still work but show as loose checkboxes on the user form.

```xml
<odoo>
    <record id="res_groups_privilege_hospital" model="res.groups.privilege">
        <field name="name">Hospital</field>
        <field name="sequence">50</field>
        <field name="category_id" ref="base.module_category_services"/>
    </record>

    <record id="group_hospital_user" model="res.groups">
        <field name="name">User</field>
        <field name="sequence">10</field>
        <field name="privilege_id" ref="res_groups_privilege_hospital"/>
        <field name="implied_ids" eval="[Command.link(ref('base.group_user'))]"/>
    </record>

    <record id="group_hospital_manager" model="res.groups">
        <field name="name">Administrator</field>
        <field name="sequence">20</field>
        <field name="privilege_id" ref="res_groups_privilege_hospital"/>
        <field name="implied_ids" eval="[Command.link(ref('group_hospital_user'))]"/>
        <field name="user_ids" eval="[Command.link(ref('base.user_root')), Command.link(ref('base.user_admin'))]"/>
    </record>
</odoo>
```

> Do not put module managers in `base.group_system` (full technical access) or `base.group_erp_manager` (access-rights administration). Define your own manager group.

### Example 2: `security/ir.model.access.csv`

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_hospital_patient_user,hospital.patient.user,model_hospital_patient,hospital.group_hospital_user,1,1,1,0
access_hospital_patient_manager,hospital.patient.manager,model_hospital_patient,hospital.group_hospital_manager,1,1,1,1
access_hospital_patient_portal,hospital.patient.portal,model_hospital_patient,base.group_portal,1,0,0,0
```

> `model_id:id` uses `model_<name with dots replaced by underscores>`, prefixed with the module only when the model is defined in another module (`account.model_account_move`).

### Example 3: Own Records for Users, Everything for Managers (`security/hospital_rules.xml`)

```xml
<odoo>
    <record id="rule_hospital_patient_user_own" model="ir.rule">
        <field name="name">Hospital Patient: users see their own</field>
        <field name="model_id" ref="model_hospital_patient"/>
        <field name="domain_force">[('user_id', '=', user.id)]</field>
        <field name="groups" eval="[Command.link(ref('group_hospital_user'))]"/>
        <field name="perm_read" eval="True"/>
        <field name="perm_write" eval="True"/>
        <field name="perm_create" eval="True"/>
        <field name="perm_unlink" eval="False"/>
    </record>

    <!-- OR-ed with the rule above, so managers are not restricted by it -->
    <record id="rule_hospital_patient_manager_all" model="ir.rule">
        <field name="name">Hospital Patient: managers see all</field>
        <field name="model_id" ref="model_hospital_patient"/>
        <field name="domain_force">[(1, '=', 1)]</field>
        <field name="groups" eval="[Command.link(ref('group_hospital_manager'))]"/>
    </record>
</odoo>
```

> `perm_*` on `ir.rule` default to `True`. Setting `perm_unlink` to `False` means this rule does not apply to delete, so delete falls back to whatever other rules say (here: only the manager rule, so users cannot delete anything). Prefer `user_id` / `partner_id` fields over `create_uid` for ownership; `create_uid` breaks the moment a manager creates a record on someone's behalf.

### Example 4: Multi-Company Record Rule

```xml
<record id="rule_hospital_patient_company" model="ir.rule">
    <field name="name">Hospital Patient: multi-company</field>
    <field name="model_id" ref="model_hospital_patient"/>
    <field name="domain_force">
        ['|', ('company_id', '=', False),
              ('company_id', 'in', company_ids)]
    </field>
</record>
```

> This rule intentionally has **no groups**, so it is global and AND-ed with every other rule. That is the pattern core modules use for company isolation. `company_ids` in a rule domain is the list of companies the user currently has **enabled** in the company switcher (`env.companies`), not every company the user is allowed to access. `company_id` (singular) is the current company.

### Example 5: Portal Rule

```xml
<record id="rule_hospital_patient_portal" model="ir.rule">
    <field name="name">Hospital Patient: portal sees own patient</field>
    <field name="model_id" ref="model_hospital_patient"/>
    <field name="domain_force">[('partner_id', 'child_of', user.partner_id.commercial_partner_id.id)]</field>
    <field name="groups" eval="[Command.link(ref('base.group_portal'))]"/>
    <field name="perm_read" eval="True"/>
    <field name="perm_write" eval="False"/>
    <field name="perm_create" eval="False"/>
    <field name="perm_unlink" eval="False"/>
</record>
```

> Portal users are not in `base.group_user`, so user-group rules and ACLs do not apply to them. They need their own ACL line (Example 2) and their own rule. `child_of` on the commercial partner lets contacts of the same company see each other's records; use `('partner_id', '=', user.partner_id.id)` for strict per-contact visibility.

### Example 6: Field-Level Access

```python
class HospitalPatient(models.Model):
    _name = 'hospital.patient'

    internal_notes = fields.Text(groups='hospital.group_hospital_manager')
    cost = fields.Monetary(groups='hospital.group_hospital_manager,base.group_system')
```

> `groups` takes a comma-separated list of group xmlids. Users outside those groups cannot read or write the field through the ORM or RPC, and it is dropped from views automatically. No JavaScript or Python override is needed.

## Debugging Workflow

1. **Read the error.** It names the model, the operation (read / write / create / unlink), and in 19 usually the record and its company. "not allowed to access" with no record is an ACL failure; "Due to security restrictions" with a record id is a record-rule failure.
2. **Check the ACL.** Query `ir.model.access` for the model (see live verification above) and confirm one line matches a group the user has, with the failing `perm_*` set.
3. **Check the user's groups.** `res.users` `has_group`, or open the user form in debug mode. Remember `implied_ids` are transitive.
4. **Check record rules.** Query `ir.rule` for the model. Apply the combine logic: every global rule must pass, at least one group rule for the user's groups must pass.
5. **Check the company.** If the record's `company_id` is not among the user's enabled companies, the multi-company global rule fails even though the user "has access" to that company. Ask them to enable it in the switcher.
6. **Check field access** if the error mentions a field: look for `groups=` on the field definition.
7. **Reproduce as that user** with `has_access` / `check_access` from `odoo-acl`, then fix the narrowest thing: add an ACL line, add a widening rule, or assign the missing group.

## Best Practices

- ✅ **Do:** Start with the most restrictive access and open up as needed.
- ✅ **Do:** Give every custom group a `privilege_id` and chain it with `implied_ids`.
- ✅ **Do:** Use `company_ids` (plural) in multi-company rules and leave the rule global.
- ✅ **Do:** Pair every restrictive group rule with a widening rule for the supervising group.
- ✅ **Do:** Test as a non-admin user. `sudo()` and `base.group_system` hide most mistakes.
- ✅ **Do:** Use `Command.link(...)` in `eval` for Many2many; `(4, ref(...))` still works but is the legacy form.
- ❌ **Don't:** Give `perm_unlink = 1` to regular users unless deletion is explicitly required.
- ❌ **Don't:** Leave `group_id` blank in `ir.model.access.csv`. It grants access to every user and is deprecated in 19.
- ❌ **Don't:** Use `base.group_system` or `base.group_erp_manager` for module managers.
- ❌ **Don't:** Forget the rule for `base.group_portal` when a model is exposed on the website or portal.

## Limitations

- **Portal and public controllers** often use `sudo()` and then filter manually; this skill covers the ORM rules, not controller-level checks.
- Record rules and ACLs are **bypassed by `sudo()`** and by `base.user_root`; any code running as superuser ignores them entirely.
- Field-level `groups=` hides the field from views and the ORM but does not stop a `sudo()`-ed compute or report from exposing its value.
- Does not cover **row-level security via PostgreSQL** (RLS); Odoo enforces everything at the ORM layer.
