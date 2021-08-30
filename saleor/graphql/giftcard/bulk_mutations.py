import graphene
from django.core.exceptions import ValidationError

from ...core.permissions import GiftcardPermissions
from ...core.tracing import traced_atomic_transaction
from ...core.utils.promo_code import generate_promo_code
from ...core.utils.validators import is_date_in_future
from ...giftcard import events, models
from ...giftcard.error_codes import GiftCardErrorCode
from ..core.descriptions import ADDED_IN_31
from ..core.mutations import BaseBulkMutation, BaseMutation, ModelBulkDeleteMutation
from ..core.types.common import GiftCardError, PriceInput
from ..core.validators import validate_price_precision
from .mutations import GiftCardCreate
from .types import GiftCard


class GiftCardBulkCreateInput(graphene.InputObjectType):
    count = graphene.Int(required=True, description="The number of cards to issue.")
    balance = graphene.Field(
        PriceInput, description="Balance of the gift card.", required=True
    )
    tag = graphene.String(description="The gift card tag.", required=True)
    expiry_date = graphene.types.datetime.Date(description="The gift card expiry date.")
    is_active = graphene.Boolean(
        required=True, description="Determine if gift card is active."
    )


class GiftCardBulkCreate(BaseMutation):
    count = graphene.Int(
        required=True,
        default_value=0,
        description="Returns how many objects were created.",
    )
    gift_cards = graphene.List(
        graphene.NonNull(GiftCard),
        required=True,
        default_value=[],
        description="List of created gift cards.",
    )

    class Arguments:
        input = GiftCardBulkCreateInput(
            required=True, description="Fields required to create gift cards."
        )

    class Meta:
        description = f"{ADDED_IN_31} Create gift cards."
        model = models.GiftCard
        permissions = (GiftcardPermissions.MANAGE_GIFT_CARD,)
        error_type_class = GiftCardError

    @classmethod
    @traced_atomic_transaction()
    def perform_mutation(cls, _root, info, **data):
        input_data = data["input"]
        cls.clean_count_value(input_data)
        cls.clean_expiry_date(input_data)
        cls.clean_balance(input_data)
        GiftCardCreate.set_created_by_user(input_data, info)
        instances = cls.create_instances(input_data, info)
        return cls(count=len(instances), gift_cards=instances)

    @staticmethod
    def clean_count_value(input_data):
        if not input_data["count"] > 0:
            raise ValidationError(
                {
                    "count": ValidationError(
                        "Count value must be greater than 0.",
                        code=GiftCardErrorCode.INVALID.value,
                    )
                }
            )

    @staticmethod
    def clean_expiry_date(input_data):
        expiry_date = input_data.get("expiry_date")
        if expiry_date and not is_date_in_future(expiry_date):
            raise ValidationError(
                {
                    "expiry_date": ValidationError(
                        "Expiry date cannot be in the past.",
                        code=GiftCardErrorCode.INVALID.value,
                    )
                }
            )

    @staticmethod
    def clean_balance(cleaned_input):
        balance = cleaned_input["balance"]
        amount = balance["amount"]
        currency = balance["currency"]
        try:
            validate_price_precision(amount, currency)
        except ValidationError as error:
            error.code = GiftCardErrorCode.INVALID.value
            raise ValidationError({"balance": error})
        if not amount > 0:
            raise ValidationError(
                {
                    "balance": ValidationError(
                        "Balance amount have to be greater than 0.",
                        code=GiftCardErrorCode.INVALID.value,
                    )
                }
            )
        cleaned_input["currency"] = currency
        cleaned_input["current_balance_amount"] = amount
        cleaned_input["initial_balance_amount"] = amount

    @staticmethod
    def create_instances(cleaned_input, info):
        count = cleaned_input.pop("count")
        balance = cleaned_input.pop("balance")
        gift_cards = models.GiftCard.objects.bulk_create(
            [
                models.GiftCard(code=generate_promo_code(), **cleaned_input)
                for _ in range(count)
            ]
        )
        events.gift_cards_issued_event(
            gift_cards, info.context.user, info.context.app, balance
        )
        return gift_cards


class GiftCardBulkDelete(ModelBulkDeleteMutation):
    class Arguments:
        ids = graphene.List(
            graphene.ID, required=True, description="List of gift card IDs to delete."
        )

    class Meta:
        description = f"{ADDED_IN_31} Delete gift cards."
        model = models.GiftCard
        permissions = (GiftcardPermissions.MANAGE_GIFT_CARD,)
        error_type_class = GiftCardError


class GiftCardBulkActivate(BaseBulkMutation):
    class Arguments:
        ids = graphene.List(
            graphene.ID, required=True, description="List of gift card IDs to activate."
        )

    class Meta:
        description = f"{ADDED_IN_31} Activate gift cards."
        model = models.GiftCard
        permissions = (GiftcardPermissions.MANAGE_GIFT_CARD,)
        error_type_class = GiftCardError

    @classmethod
    @traced_atomic_transaction()
    def bulk_action(cls, info, queryset):
        queryset = queryset.filter(is_active=False)
        gift_card_ids = [gift_card.id for gift_card in queryset]
        queryset.update(is_active=True)
        events.gift_cards_activated_event(
            gift_card_ids, user=info.context.user, app=info.context.app
        )


class GiftCardBulkDeactivate(BaseBulkMutation):
    class Arguments:
        ids = graphene.List(
            graphene.ID,
            required=True,
            description="List of gift card IDs to deactivate.",
        )

    class Meta:
        description = f"{ADDED_IN_31} Deactivate gift cards."
        model = models.GiftCard
        permissions = (GiftcardPermissions.MANAGE_GIFT_CARD,)
        error_type_class = GiftCardError

    @classmethod
    @traced_atomic_transaction()
    def bulk_action(cls, info, queryset):
        queryset = queryset.filter(is_active=True)
        gift_card_ids = [gift_card.id for gift_card in queryset]
        queryset.update(is_active=False)
        events.gift_cards_deactivated_event(
            gift_card_ids, user=info.context.user, app=info.context.app
        )