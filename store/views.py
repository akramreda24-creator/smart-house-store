import csv
from datetime import timedelta
from decimal import Decimal
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Product, Order, OrderItem


def home(request):
    products = Product.objects.filter(is_active=True)
    return render(request, "store/home.html", {
        "products": products,
        "featured": products.filter(is_featured=True)[:4],
    })

def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk, is_active=True)
    return render(request, "store/product_detail.html", {"product": product})

@require_POST
def add_to_cart(request, pk):
    product = get_object_or_404(Product, pk=pk, is_active=True)
    cart = request.session.get("cart", {})
    key = str(product.pk)
    cart[key] = int(cart.get(key, 0)) + 1
    request.session["cart"] = cart
    messages.success(request, "تمت إضافة المنتج إلى السلة.")
    return redirect(request.META.get("HTTP_REFERER", "cart"))

@require_POST
def remove_from_cart(request, pk):
    cart = request.session.get("cart", {})
    cart.pop(str(pk), None)
    request.session["cart"] = cart
    return redirect("cart")

def cart(request):
    cart_data = request.session.get("cart", {})
    items, total = [], Decimal("0.00")
    for product_id, quantity in cart_data.items():
        product = Product.objects.filter(pk=product_id, is_active=True).first()
        if product:
            subtotal = product.current_price * int(quantity)
            items.append({"product": product, "quantity": int(quantity), "subtotal": subtotal})
            total += subtotal
    return render(request, "store/cart.html", {"items": items, "total": total})

def checkout(request):
    cart_data = request.session.get("cart", {})
    if request.method == "POST":
        if not cart_data:
            messages.error(request, "السلة فارغة.")
            return redirect("cart")
        name = request.POST.get("customer_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        address = request.POST.get("address", "").strip()
        notes = request.POST.get("notes", "").strip()
        if not name or not phone or not address:
            messages.error(request, "من فضلك أكمل الاسم ورقم الهاتف والعنوان.")
            return redirect("checkout")
        order = Order.objects.create(customer_name=name, phone=phone, address=address, notes=notes)
        total = Decimal("0.00")
        for product_id, quantity in cart_data.items():
            product = Product.objects.filter(pk=product_id, is_active=True).first()
            if product and int(quantity) > 0:
                price = product.current_price
                OrderItem.objects.create(order=order, product=product, quantity=int(quantity), unit_price=price)
                total += price * int(quantity)
        order.total = total
        order.save(update_fields=["total"])
        request.session["cart"] = {}
        return render(request, "store/order_success.html", {"order": order})
    return render(request, "store/checkout.html")

def contact(request):
    return render(request, "store/contact.html")


# ---------------- Custom Smart House dashboard ----------------
from django.contrib.auth.decorators import user_passes_test
from django.forms import ModelForm
from django.utils.text import slugify

class ProductForm(ModelForm):
    class Meta:
        model = Product
        fields = ["name", "slug", "description", "price", "sale_price",
                  "image", "stock", "is_active", "is_featured"]

def staff_required(view_func):
    return user_passes_test(lambda u: u.is_authenticated and u.is_staff,
                            login_url="/admin/login/")(view_func)

@staff_required
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=6)

    valid_orders = Order.objects.exclude(status="cancelled")
    weekly_values = (
        valid_orders.filter(created_at__date__gte=week_start, created_at__date__lte=today)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(value=Sum("total"))
        .order_by("day")
    )
    sales_by_day = {row["day"]: row["value"] for row in weekly_values}
    max_daily_value = max(sales_by_day.values(), default=Decimal("0.00"))
    sales_chart = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        value = sales_by_day.get(day, Decimal("0.00"))
        height = round(float(value / max_daily_value) * 100) if max_daily_value else 0
        sales_chart.append({
            "label": day.strftime("%d/%m"),
            "value": value,
            "height": max(height, 3) if value else 0,
            "is_today": day == today,
        })

    month_orders = valid_orders.filter(created_at__date__gte=month_start, created_at__date__lte=today)
    month_value = month_orders.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    status_counts = dict(Order.objects.values("status").annotate(count=Count("id")).values_list("status", "count"))
    low_stock = Product.objects.filter(is_active=True, stock__lte=5)
    best_sellers = (
        OrderItem.objects.filter(order__created_at__date__gte=month_start, order__created_at__date__lte=today)
        .exclude(order__status="cancelled")
        .values("product__id", "product__name")
        .annotate(units=Sum("quantity"))
        .order_by("-units")[:5]
    )
    context = {
        "products_count": Product.objects.count(),
        "orders_count": Order.objects.count(),
        "active_products_count": Product.objects.filter(is_active=True).count(),
        "orders_today_count": Order.objects.filter(created_at__date=today).count(),
        "new_orders_count": status_counts.get("new", 0),
        "confirmed_orders_count": status_counts.get("confirmed", 0),
        "shipped_orders_count": status_counts.get("shipped", 0),
        "month_orders_count": month_orders.count(),
        "month_value": month_value,
        "low_stock_count": low_stock.count(),
        "low_stock_products": low_stock.order_by("stock", "name")[:6],
        "recent_orders": Order.objects.all()[:8],
        "sales_chart": sales_chart,
        "best_sellers": best_sellers,
        "active": "home",
    }
    return render(request, "store/dashboard/home.html", context)

@staff_required
def dashboard_products(request):
    products = Product.objects.all()
    query = request.GET.get("q", "").strip()
    visibility = request.GET.get("visibility", "")
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(slug__icontains=query))
    if visibility == "active":
        products = products.filter(is_active=True)
    elif visibility == "hidden":
        products = products.filter(is_active=False)
    elif visibility == "low":
        products = products.filter(is_active=True, stock__lte=5)

    sort = request.GET.get("sort", "newest")
    ordering = {
        "newest": "-created_at",
        "name": "name",
        "price_low": "price",
        "stock_low": "stock",
    }.get(sort, "-created_at")
    page_obj = Paginator(products.order_by(ordering, "pk"), 20).get_page(request.GET.get("page"))
    return render(request, "store/dashboard/products.html", {
        "products": page_obj.object_list,
        "page_obj": page_obj,
        "query": query,
        "visibility": visibility,
        "sort": sort,
        "products_count": Product.objects.count(),
        "active_products_count": Product.objects.filter(is_active=True).count(),
        "hidden_products_count": Product.objects.filter(is_active=False).count(),
        "low_stock_count": Product.objects.filter(is_active=True, stock__lte=5).count(),
        "active": "products",
    })

@staff_required
def dashboard_product_add(request):
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save(commit=False)
            if not product.slug:
                product.slug = slugify(product.name, allow_unicode=True) or f"product-{Product.objects.count()+1}"
            product.save()
            messages.success(request, "تمت إضافة المنتج بنجاح.")
            return redirect("dashboard_products")
    else:
        form = ProductForm()
    return render(request, "store/dashboard/product_form.html", {"form": form, "page_title": "إضافة منتج جديد", "active": "products"})

@staff_required
def dashboard_product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث المنتج بنجاح.")
            return redirect("dashboard_products")
    else:
        form = ProductForm(instance=product)
    return render(request, "store/dashboard/product_form.html", {"form": form, "page_title": "تعديل المنتج", "active": "products"})

@staff_required
@require_POST
def dashboard_product_toggle(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    state = "ظهر في المتجر" if product.is_active else "أُخفي من المتجر"
    messages.success(request, f"تم تحديث ظهور المنتج: {state}.")
    return redirect("dashboard_products")

@staff_required
@require_POST
def dashboard_product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.delete()
    messages.success(request, "تم حذف المنتج.")
    return redirect("dashboard_products")

@staff_required
def dashboard_orders(request):
    orders = Order.objects.prefetch_related("items__product").all()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        if query.isdigit():
            orders = orders.filter(Q(pk=int(query)) | Q(phone__icontains=query) | Q(customer_name__icontains=query))
        else:
            orders = orders.filter(Q(customer_name__icontains=query) | Q(phone__icontains=query))
    if status in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status)

    if request.GET.get("download") == "csv":
        return _orders_csv(orders)

    page_obj = Paginator(orders, 20).get_page(request.GET.get("page"))
    status_counts = dict(Order.objects.values("status").annotate(count=Count("id")).values_list("status", "count"))
    return render(request, "store/dashboard/orders.html", {
        "orders": page_obj.object_list,
        "page_obj": page_obj,
        "query": query,
        "selected_status": status,
        "status_choices": Order.STATUS_CHOICES,
        "status_counts": status_counts,
        "orders_count": Order.objects.count(),
        "active": "orders",
    })


def _csv_cell(value):
    text = str(value or "")
    if text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _orders_csv(orders):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = f'attachment; filename="smart-house-orders-{timezone.localdate():%Y%m%d}.csv"'
    writer = csv.writer(response)
    writer.writerow(["رقم الطلب", "التاريخ", "اسم العميل", "الهاتف", "العنوان", "المنتجات", "الإجمالي", "الحالة"])
    for order in orders.iterator(chunk_size=200):
        items = ", ".join(
            f"{item.product.name} × {item.quantity}"
            for item in order.items.all()
        )
        writer.writerow([
            order.pk,
            timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
            _csv_cell(order.customer_name),
            _csv_cell(order.phone),
            _csv_cell(order.address),
            _csv_cell(items),
            order.total,
            order.get_status_display(),
        ])
    return response

@staff_required
def dashboard_order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related("items__product"), pk=pk)
    item_rows = [{"item": item, "subtotal": item.unit_price * item.quantity} for item in order.items.all()]
    return render(request, "store/dashboard/order_detail.html", {
        "order": order,
        "item_rows": item_rows,
        "status_choices": Order.STATUS_CHOICES,
        "active": "orders",
    })

@staff_required
def dashboard_customers(request):
    customers = Order.objects.values("customer_name", "phone", "address").annotate(
        orders_count=Count("id"), total_spent=Sum("total")
    ).order_by("-orders_count")
    query = request.GET.get("q", "").strip()
    if query:
        customers = customers.filter(Q(customer_name__icontains=query) | Q(phone__icontains=query))
    page_obj = Paginator(customers, 25).get_page(request.GET.get("page"))
    return render(request, "store/dashboard/customers.html", {
        "customers": page_obj.object_list,
        "page_obj": page_obj,
        "query": query,
        "active": "customers",
    })

@staff_required
@require_POST
def dashboard_order_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    status = request.POST.get("status")
    valid_statuses = dict(Order.STATUS_CHOICES)
    if status in valid_statuses:
        order.status = status
        order.save(update_fields=["status"])
        messages.success(request, f"تم تحديث حالة الطلب #{order.pk}.")
    else:
        messages.error(request, "حالة الطلب غير صحيحة.")
    if request.POST.get("return_to") == "detail":
        return redirect("dashboard_order_detail", pk=pk)
    return redirect("dashboard_orders")

