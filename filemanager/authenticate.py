from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST

def login_view(request):
    if request.user.is_authenticated:
        return redirect("filemanager:home")
        
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            
            # "One time login" - set session to expire in 10 years
            request.session.set_expiry(315360000) 
            
            messages.success(request, f"Welcome back, {user.username}.")
            return redirect("filemanager:home")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
        
    return render(request, "filemanager/auth/login.html", {"form": form})


def register_view(request):
    if request.user.is_authenticated:
        return redirect("filemanager:home")

    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            
            # "One time login" - set session to expire in 10 years
            request.session.set_expiry(315360000)
            
            messages.success(request, "Account created successfully. Welcome!")
            return redirect("filemanager:home")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserCreationForm()

    return render(request, "filemanager/auth/register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("filemanager:login")


@login_required
def change_password_view(request):
    # Only the 'admin' account can change passwords
    if request.user.username != "admin" and not request.user.is_superuser:
        return HttpResponseForbidden("Only the admin account can change passwords.")

    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # Keep the user logged in after password change
            update_session_auth_hash(request, user)
            messages.success(request, "Your password was successfully updated!")
            return redirect("filemanager:home")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "filemanager/auth/change_password.html", {"form": form})