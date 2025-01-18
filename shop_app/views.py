from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import Product, Cart, CartItem, Transaction
from .serializers import ProductSerializer, DetailedProductSerializer, CartSerializer, CartItemSerializer, SimpleCartSerializer, UserSerializer
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from decimal import Decimal
import uuid
from django.conf import settings
from django.shortcuts import get_object_or_404
import requests
from .models import Transaction
import paypalrestsdk
from rest_framework import generics
from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny

# Create your views here.

BASE_URL = settings.REACT_BASE_URL


paypalrestsdk.configure({
    "mode": settings.PAYPAL_MODE,
    "client_id": settings.PAYPAL_CLIENT_ID,
    "client_secret": settings.PAYPAL_CLIENT_SECRET
})

@api_view(['GET'])
def products(request):
    products = Product.objects.all()
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def product_detail(request, slug):
    product = Product.objects.get(slug=slug)
    serializer = DetailedProductSerializer(product)
    return Response(serializer.data)

@api_view(['POST'])
def add_item(request):
    try:
        cart_code = request.data.get('cart_code')
        product_id = request.data.get('product_id')

        cart, created = Cart.objects.get_or_create(cart_code=cart_code)
        product = Product.objects.get(id=product_id)

        cartitem, created = CartItem.objects.get_or_create(cart=cart, product=product)
        cartitem.quantity = 1
        cartitem.save()

        serializer = CartItemSerializer(cartitem)
        return Response({"data": serializer.data, "message": "Item added to cart"}, status=201)
    except Exception as e:
        return Response({"error": str(e)}, status=400)    

@api_view(['GET'])
def product_in_cart(request):
    cart_code = request.query_params.get('cart_code')
    product_id = request.query_params.get('product_id')

    cart = Cart.objects.get(cart_code=cart_code)
    product = Product.objects.get(id=product_id)

    product_exists_in_cart = CartItem.objects.filter(cart=cart, product=product).exists()

    return Response({'product_in_cart': product_exists_in_cart})


@api_view(['GET'])
def get_cart_start(request):
    cart_code = request.query_params.get('cart_code')
    cart = Cart.objects.get(cart_code=cart_code, paid=False)
    serializer = SimpleCartSerializer(cart)
    return Response(serializer.data)

@api_view(['GET'])
def get_cart(request):
    cart_code = request.query_params.get('cart_code')
    cart = Cart.objects.get(cart_code=cart_code, paid=False)
    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['PATCH'])
def update_quantity(request):
    try:
        cartitem_id = request.data.get('item_id')
        quantity = request.data.get('quantity')
        quantity = int(quantity)
        cartitem = CartItem.objects.get(id=cartitem_id)
        cartitem.quantity = quantity
        cartitem.save()
        serializer = CartItemSerializer(cartitem)
        return Response({ "data": serializer.data, "message": "CartItem updated successfully" }, status=200)

    except Exception as e:
        return Response({"error": str(e)}, status=400)

@api_view(['POST'])
def delete_cartitem(request):
    cartitem_id = request.data.get("item_id")
    cartitem = CartItem.objects.get(id=cartitem_id)
    cartitem.delete()
    return Response({"message": "Item deleted successfully"}, status=status.HTTP_204_NO_CONTENT)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_username(request):
    user = request.user
    return Response({"username": user.username})

class RegisterPageView(generics.CreateAPIView):
    queryset = get_user_model().objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]  # Allow any user to register

    def perform_create(self, serializer):
        """
        Override perform_create to handle user registration and related actions.
        """
        user = serializer.save()

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_info(request):
    user = request.user
    serializer = UserSerializer(user)
    return Response(serializer.data)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def initiate_payment(request):
    if request.user:
        try:
            tx_ref = str(uuid.uuid4())
            cart_code = request.data.get("cart_code")
            cart = Cart.objects.get(cart_code=cart_code)
            user = request.user

            amount = sum([item.quantity * item.product.price for item in cart.items.all()])
            tax = Decimal("4.00")
            total_amount = amount + tax
            currency = "NGN"
            redirect_url = f"{BASE_URL}/payment-status/"

            transaction = Transaction.objects.create(
                ref=tx_ref,
                cart=cart,
                amount=total_amount,
                currency=currency,
                user=user,
                status='pending'
            )

            flutterwave_payload = {
                "tx_ref": tx_ref,
                "amount": str(total_amount),
                "currency": currency,
                "redirect_url": redirect_url,
                "customer": {
                    "email": user.email,
                    "name": user.username,
                    "phonenumber": user.phone
            },
            "customizations": {
                "title": "Shopping Payment"
                }
            }

            headers = {
                "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                'https://api.flutterwave.com/v3/payments',
                json=flutterwave_payload,
                headers=headers,
            )

            if response.status_code == 200:
                return Response(response.json(), status=status.HTTP_200_OK)
            else:
                return Response(response.json(), status=status_code)
        except requests.exceptions.RequestException as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def payment_callback(request):
    status_param = request.data.get('status')
    tx_ref = request.data.get('tx_ref')
    transaction_id = request.data.get('transaction_id')

    user = request.user

    # Validate required parameters
    if not (status_param and tx_ref and transaction_id):
        return Response(
            {"message": "Invalid request", "subMessage": "Missing required parameters."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if status_param == 'successful':
        headers = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
        }

        try:
            # Verify the transaction with Flutterwave
            response = requests.get(
                f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            response_data = response.json()
        except requests.exceptions.RequestException as e:
            # Handle network or request-related errors
            return Response(
                {"message": "Verification error", "subMessage": "Failed to verify transaction with Flutterwave."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if response_data.get('status') == 'success':
            try:
                # Fetch the transaction record from the database
                transaction = Transaction.objects.get(ref=tx_ref)

                # Validate transaction details
                data = response_data.get('data', {})
                if (data.get('status') == "successful" and
                        float(data.get('amount')) == float(transaction.amount) and
                        data.get('currency') == transaction.currency):

                    # Mark transaction as completed
                    transaction.status = 'completed'
                    transaction.save()

                    # Update cart
                    cart = transaction.cart
                    cart.paid = True
                    cart.user = user
                    cart.save()

                    return Response(
                        {"message": "Payment successful!", "subMessage": "You have successfully made the payment."},
                        status=status.HTTP_200_OK
                    )
                else:
                    return Response(
                        {"message": "Payment verification failed.", "subMessage": "Transaction details mismatch."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Transaction.DoesNotExist:
                return Response(
                    {"message": "Transaction not found", "subMessage": "No matching transaction found for the provided reference."},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            return Response(
                {"message": "Verification failed", "subMessage": response_data.get('message', "Unknown error.")},
                status=status.HTTP_400_BAD_REQUEST
            )
    else:
        return Response(
            {"message": "Payment was not successful.", "subMessage": "The payment status was unsuccessful."},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(['POST'])
def initiate_paypal_payment(request):
    if request.method == 'POST' and request.user.is_authenticated:
        tx_ref = str(uuid.uuid4())
        user = request.user
        cart_code = request.data.get("cart_code")
        cart = Cart.objects.get(cart_code=cart_code)
        amount = sum(item.product.price * item.quantity for item in cart.items.all())
        tax = Decimal("4.00")
        total_amount = amount + tax

        payment = paypalrestsdk.Payment({
            "intent": "sale",
            "payer": {
                "payment_method": "paypal"
            },
            "redirect_urls": {
                "return_url": f"{BASE_URL}/payment-status?paymentStatus=success&ref={tx_ref}",
                "cancel_url": f"{BASE_URL}/payment-status?paymentStatus=cancel"
            },
            "transactions": [{
                "item_list": {
                    "items": [{
                        "name": "Cart Items",
                        "sku": "cart",
                        "price": str(total_amount),
                        "currency": "USD",
                        "quantity": 1
                        }]
                    },
                    "amount": {
                        "total": str(total_amount),
                        "currency": "USD"
                    },
                    "description": "Payment for cart items"
            }]
        })

        print("pay_id", payment)

        transaction, created = Transaction.objects.get_or_create(
            ref=tx_ref,
            cart=cart,
            amount=total_amount,
            user=user,
            status="pending",
        )

        if payment.create():
            for link in payment.links:
                if link.rel == "approval_url":
                    approval_url = str(link.href)
                    return Response({"approval_url": approval_url})

        else:
            return Response({"error": payment.erro}, status=400)

    return Response({"error": "Invalid request"}, status=400)

@api_view(['POST'])
def paypal_payment_callback(request):
    payment_id = request.query_params.get('paymentId')
    payer_id = request.query_params.get('PayerID')
    ref = request.query_params.get('ref')

    user = request.user

    print("refff", ref)

    transaction = Transaction.objects.get(ref=ref)

    if payment_id and payer_id:
        payment = paypalrestsdk.Payment.find(payment_id)

        transaction.status = 'completed'
        transaction.save()
        cart = transaction.cart
        cart.paid = True
        cart.user = user
        cart.save()

        return Response({'message': 'Payment successful', 'subMessage': 'You have successfully made payment for the items you purchased '})

    else:
        return Response({'message': 'Invalid payment details.'}, status=400)
