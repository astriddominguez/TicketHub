from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .roles import get_roles

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def validate_password(self, value: str) -> str:
        # Same rules as the admin (length, too common, all numeric...).
        validate_password(value)
        return value

    def create(self, validated_data):
        # create_user hashes the password; never store it in plain text.
        return User.objects.create_user(**validated_data)


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "roles"]

    def get_roles(self, user) -> list[str]:
        return get_roles(user)


class RoleTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Other services (booking) read the roles from the token without calling us.
        token["roles"] = get_roles(user)
        return token
