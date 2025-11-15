"""
Payment System Exceptions
Custom exceptions for payment processing.
"""


class PaymentException(Exception):
    """Base exception for payment-related errors."""
    pass


class PaymentGatewayException(PaymentException):
    """Exception raised when payment gateway returns an error."""
    def __init__(self, message, gateway_response=None):
        super().__init__(message)
        self.gateway_response = gateway_response


class PaymentValidationException(PaymentException):
    """Exception raised when payment data validation fails."""
    pass


class PaymentNotFoundException(PaymentException):
    """Exception raised when a payment record is not found."""
    pass


class PaymentAmountMismatchException(PaymentException):
    """Exception raised when payment amount doesn't match order total."""
    pass


class PaymentMethodNotSupportedException(PaymentException):
    """Exception raised when an unsupported payment method is used."""
    pass


class PaymentGatewayNotConfiguredException(PaymentException):
    """Exception raised when payment gateway is not properly configured."""
    pass


class PaymentTimeoutException(PaymentException):
    """Exception raised when payment processing times out."""
    pass


class PaymentRefundException(PaymentException):
    """Exception raised when payment refund fails."""
    pass

