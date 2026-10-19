from next import page


metadata = {"breadcrumb": "Dashboard"}

page.login_required(inherit=True)
page.permission_required("shadcn_admin.use_admin", inherit=True)
page.permission_required("admin_audit.view_adminactivitylog", inherit=True)
