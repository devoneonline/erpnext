# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe, os
from frappe import _

from frappe.utils import cint
import frappe.defaults


from frappe.model.document import Document

class Company(Document):
	def onload(self):
		self.get("__onload")["transactions_exist"] = self.check_if_transactions_exist()

	def check_if_transactions_exist(self):
		exists = False
		for doctype in ["Sales Invoice", "Delivery Note", "Sales Order", "Quotation",
			"Purchase Invoice", "Purchase Receipt", "Purchase Order", "Supplier Quotation"]:
				if frappe.db.sql("""select name from `tab%s` where company=%s and docstatus=1
					limit 1""" % (doctype, "%s"), self.name):
						exists = True
						break

		return exists

	def validate(self):
		self.validate_abbr()
		self.validate_default_accounts()
		self.validate_currency()

	def validate_abbr(self):
		self.abbr = self.abbr.strip()

		if self.get('__islocal') and len(self.abbr) > 5:
			frappe.throw(_("Abbreviation cannot have more than 5 characters"))

		if not self.abbr.strip():
			frappe.throw(_("Abbreviation is mandatory"))

		if frappe.db.sql("select abbr from tabCompany where name!=%s and abbr=%s", (self.name, self.abbr)):
			frappe.throw(_("Abbreviation already used for another company"))

	def validate_default_accounts(self):
		for field in ["default_bank_account", "default_cash_account", "default_receivable_account", "default_payable_account",
			"default_expense_account", "default_income_account", "stock_received_but_not_billed",
			"stock_adjustment_account", "expenses_included_in_valuation"]:
				if self.get(field):
					for_company = frappe.db.get_value("Account", self.get(field), "company")
					if for_company != self.name:
						frappe.throw(_("Account {0} does not belong to company: {1}")
							.format(self.get(field), self.name))

	def validate_currency(self):
		self.previous_default_currency = frappe.db.get_value("Company", self.name, "default_currency")
		if self.default_currency and self.previous_default_currency and \
			self.default_currency != self.previous_default_currency and \
			self.check_if_transactions_exist():
				frappe.throw(_("Cannot change company's default currency, because there are existing transactions. Transactions must be cancelled to change the default currency."))

	def on_update(self):
		if not frappe.db.sql("""select name from tabAccount
				where company=%s and docstatus<2 limit 1""", self.name):
			if not frappe.local.flags.ignore_chart_of_accounts:
				self.create_default_accounts()
				self.create_default_warehouses()
			
				self.install_country_fixtures()

		if not frappe.db.get_value("Cost Center", {"is_group": 0, "company": self.name}):
			self.create_default_cost_center()

		if not frappe.local.flags.ignore_chart_of_accounts:
			self.set_default_accounts()

		if self.default_currency:
			frappe.db.set_value("Currency", self.default_currency, "enabled", 1)

		frappe.clear_cache()

	def install_country_fixtures(self):
		path = os.path.join(os.path.dirname(__file__), "fixtures", self.country.lower())
		if os.path.exists(path.encode("utf-8")):
			frappe.get_attr("erpnext.setup.doctype.company.fixtures.{0}.install"
				.format(self.country.lower()))(self)

	def create_default_warehouses(self):
		for wh_detail in [
			{"warehouse_name": _("All Warehouses"), "is_group": 1},
			{"warehouse_name": _("Stores"), "is_group": 0},
			{"warehouse_name": _("Work In Progress"), "is_group": 0},
			{"warehouse_name": _("Finished Goods"), "is_group": 0}]:
			
			if not frappe.db.exists("Warehouse", "{0} - {1}".format(wh_detail["warehouse_name"], self.abbr)):
				stock_group = frappe.db.get_value("Account", {"account_type": "Stock",
					"is_group": 1, "company": self.name})
				if stock_group:
					warehouse = frappe.get_doc({
						"doctype":"Warehouse",
						"warehouse_name": wh_detail["warehouse_name"],
						"is_group": wh_detail["is_group"],
						"company": self.name,
						"parent_warehouse": "{0} - {1}".format(_("All Warehouses"), self.abbr) \
							if not wh_detail["is_group"] else "",
						"create_account_under": stock_group
					})
					warehouse.flags.ignore_permissions = True
					warehouse.insert()

	def create_default_accounts(self):
		if not self.chart_of_accounts:
			self.chart_of_accounts = "Standard"

		from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts import create_charts
		create_charts(self.chart_of_accounts, self.name)

		frappe.db.set(self, "default_receivable_account", frappe.db.get_value("Account",
			{"company": self.name, "account_type": "Receivable", "is_group": 0}))
		frappe.db.set(self, "default_payable_account", frappe.db.get_value("Account",
			{"company": self.name, "account_type": "Payable", "is_group": 0}))

	def set_default_accounts(self):
		self._set_default_account("default_cash_account", "Cash")
		self._set_default_account("default_bank_account", "Bank")
		self._set_default_account("round_off_account", "Round Off")
		self._set_default_account("accumulated_depreciation_account", "Accumulated Depreciation")
		self._set_default_account("depreciation_expense_account", "Depreciation")

		if cint(frappe.db.get_single_value("Accounts Settings", "auto_accounting_for_stock")):
			self._set_default_account("stock_received_but_not_billed", "Stock Received But Not Billed")
			self._set_default_account("stock_adjustment_account", "Stock Adjustment")
			self._set_default_account("expenses_included_in_valuation", "Expenses Included In Valuation")
			self._set_default_account("default_expense_account", "Cost of Goods Sold")

		if not self.default_income_account:
			self.db_set("default_income_account", frappe.db.get_value("Account",
				{"account_name": _("Sales"), "company": self.name}))


	def _set_default_account(self, fieldname, account_type):
		if self.get(fieldname):
			return

		account = frappe.db.get_value("Account", {"account_type": account_type,
			"is_group": 0, "company": self.name})

		if account:
			self.db_set(fieldname, account)

	def create_default_cost_center(self):
		cc_list = [
			{
				'cost_center_name': self.name,
				'company':self.name,
				'is_group': 1,
				'parent_cost_center':None
			},
			{
				'cost_center_name':_('Main'),
				'company':self.name,
				'is_group':0,
				'parent_cost_center':self.name + ' - ' + self.abbr
			},
		]
		for cc in cc_list:
			cc.update({"doctype": "Cost Center"})
			cc_doc = frappe.get_doc(cc)
			cc_doc.flags.ignore_permissions = True

			if cc.get("cost_center_name") == self.name:
				cc_doc.flags.ignore_mandatory = True
			cc_doc.insert()

		frappe.db.set(self, "cost_center", _("Main") + " - " + self.abbr)
		frappe.db.set(self, "round_off_cost_center", _("Main") + " - " + self.abbr)
		frappe.db.set(self, "depreciation_cost_center", _("Main") + " - " + self.abbr)

	def before_rename(self, olddn, newdn, merge=False):
		if merge:
			frappe.throw(_("Sorry, companies cannot be merged"))

	def after_rename(self, olddn, newdn, merge=False):
		frappe.db.set(self, "company_name", newdn)

		frappe.db.sql("""update `tabDefaultValue` set defvalue=%s
			where defkey='Company' and defvalue=%s""", (newdn, olddn))

		frappe.defaults.clear_cache()

	def abbreviate(self):
		self.abbr = ''.join([c[0].upper() for c in self.company_name.split()])

	def on_trash(self):
		"""
			Trash accounts and cost centers for this company if no gl entry exists
		"""
		accounts = frappe.db.sql_list("select name from tabAccount where company=%s", self.name)
		cost_centers = frappe.db.sql_list("select name from `tabCost Center` where company=%s", self.name)
		warehouses = frappe.db.sql_list("select name from tabWarehouse where company=%s", self.name)

		rec = frappe.db.sql("SELECT name from `tabGL Entry` where company = %s", self.name)
		if not rec:
			frappe.db.sql("""delete from `tabBudget Account`
				where exists(select name from tabBudget 
					where name=`tabBudget Account`.parent and company = %s)""", self.name)
			
			for doctype in ["Account", "Cost Center", "Budget", "Party Account"]:
				frappe.db.sql("delete from `tab{0}` where company = %s".format(doctype), self.name)

		if not frappe.db.get_value("Stock Ledger Entry", {"company": self.name}):
			frappe.db.sql("""delete from `tabWarehouse` where company=%s""", self.name)

		frappe.defaults.clear_default("company", value=self.name)

		# clear default accounts, warehouses from item
		if warehouses:

			for f in ["default_warehouse", "website_warehouse"]:
				frappe.db.sql("""update tabItem set %s=NULL where %s in (%s)"""
					% (f, f, ', '.join(['%s']*len(warehouses))), tuple(warehouses))

			frappe.db.sql("""delete from `tabItem Reorder` where warehouse in (%s)"""
				% ', '.join(['%s']*len(warehouses)), tuple(warehouses))

		if accounts:
			for f in ["income_account", "expense_account"]:
				frappe.db.sql("""update tabItem set %s=NULL where %s in (%s)"""
					% (f, f, ', '.join(['%s']*len(accounts))), tuple(accounts))

		if cost_centers:
			for f in ["selling_cost_center", "buying_cost_center"]:
				frappe.db.sql("""update tabItem set %s=NULL where %s in (%s)"""
					% (f, f, ', '.join(['%s']*len(cost_centers))), tuple(cost_centers))

		# reset default company
		frappe.db.sql("""update `tabSingles` set value=""
			where doctype='Global Defaults' and field='default_company'
			and value=%s""", self.name)

@frappe.whitelist()
def replace_abbr(company, old, new):
	new = new.strip()
	if not new:
		frappe.throw(_("Abbr can not be blank or space"))

	frappe.only_for("System Manager")

	frappe.db.set_value("Company", company, "abbr", new)

	def _rename_record(dt):
		for d in frappe.db.sql("select name from `tab%s` where company=%s" % (dt, '%s'), company):
			parts = d[0].rsplit(" - ", 1)
			if len(parts) == 1 or parts[1].lower() == old.lower():
				frappe.rename_doc(dt, d[0], parts[0] + " - " + new)

	for dt in ["Account", "Cost Center", "Warehouse"]:
		_rename_record(dt)
		frappe.db.commit()

def get_name_with_abbr(name, company):
	company_abbr = frappe.db.get_value("Company", company, "abbr")
	parts = name.split(" - ")

	if parts[-1].lower() != company_abbr.lower():
		parts.append(company_abbr)

	return " - ".join(parts)

def get_company_currency(company):
	return frappe.local_cache("company_currency", company,
		lambda: frappe.db.get_value("Company", company, "default_currency"))
