def cart_count(request):
    cart = request.session.get("cart", {})
    return {"cart_count": sum(int(q) for q in cart.values())}


def admin_summary(request):
    if request.user.is_authenticated and request.user.is_staff:
        from .models import Order

        return {"admin_new_orders": Order.objects.filter(status="new").count()}
    return {"admin_new_orders": 0}

