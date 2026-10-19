from next.pages import proxy


proxy.login_required()
proxy.permission_required("shadcn_admin.use_admin")
